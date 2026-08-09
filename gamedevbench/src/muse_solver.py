#!/usr/bin/env python3
"""Meta Muse CLI solver for GameDevBench."""

import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from typing import Dict, Iterable, Optional, Tuple
import uuid

from gamedevbench.src.base_solver import BaseSolver
from gamedevbench.src.utils.data_types import SolverResult, TokenUsage


class MuseSolver(BaseSolver):
    """Run a task with the Muse coding agent in headless JSON mode."""

    SUPPORTS_MCP = False
    SUPPORTS_SYSTEM_PROMPT = False
    SUPPORTS_EFFORT = True

    # USD per million tokens. Muse's downloaded provider catalog is preferred;
    # this exact-model fallback matches Meta's catalog as of 2026-08-09.
    # Sources: https://dev.meta.ai/docs/models/ and
    # https://dev.meta.ai/docs/prompt-caching/
    _FALLBACK_MODEL_PRICING = {
        "muse-spark-1.2": {"input": 1.25, "output": 4.25, "cached": 0.15},
    }

    def __init__(
        self,
        timeout_seconds: Optional[int] = 600,
        debug: bool = False,
        use_runtime_video: bool = False,
        model: Optional[str] = None,
        effort: Optional[str] = None,
    ):
        super().__init__(timeout_seconds, debug, False, use_runtime_video)
        self.model = model
        self.effort = effort

    @staticmethod
    def is_rate_limit_error(error_message: str) -> bool:
        error_lower = error_message.lower()
        return bool(re.search(r"\b429\b", error_lower)) or any(
            marker in error_lower
            for marker in (
                "rate limit",
                "rate_limit",
                "ratelimit",
                "quota exceeded",
                "too many requests",
            )
        )

    @staticmethod
    def _events(output: str) -> Iterable[dict]:
        for line in output.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                yield event

    @staticmethod
    def _as_text(value) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)

    @staticmethod
    def _coerce_nonnegative_int(value) -> int:
        if isinstance(value, bool) or value is None:
            return 0
        try:
            return max(0, int(value))
        except (TypeError, ValueError, OverflowError):
            return 0

    @classmethod
    def _usage_from_mapping(cls, mapping) -> Optional[TokenUsage]:
        """Normalize Meta Responses, Muse logs, and compatible usage shapes."""
        if not isinstance(mapping, dict):
            return None

        input_details = mapping.get("input_tokens_details")
        if not isinstance(input_details, dict):
            input_details = mapping.get("prompt_tokens_details")
        if not isinstance(input_details, dict):
            input_details = {}

        input_tokens = cls._coerce_nonnegative_int(
            mapping.get("input_tokens")
            or mapping.get("prompt_tokens")
            or mapping.get("inputTokenCount")
            or mapping.get("promptTokenCount")
        )
        output_tokens = cls._coerce_nonnegative_int(
            mapping.get("output_tokens")
            or mapping.get("completion_tokens")
            or mapping.get("outputTokenCount")
            or mapping.get("candidatesTokenCount")
        )
        total_tokens = cls._coerce_nonnegative_int(
            mapping.get("total_tokens") or mapping.get("totalTokenCount")
        )

        generic_cached = cls._coerce_nonnegative_int(
            mapping.get("cached_tokens")
            or mapping.get("cachedTokens")
            or input_details.get("cached_tokens")
            or input_details.get("cachedTokens")
        )
        cache_read_tokens = cls._coerce_nonnegative_int(
            mapping.get("cache_read_tokens")
            or mapping.get("cache_read_input_tokens")
        )
        # Meta's Responses API exposes cache hits as
        # input_tokens_details.cached_tokens. Muse's durable event also carries
        # cache_read_tokens, so use the larger normalized value without adding
        # aliases together.
        cache_read_tokens = max(cache_read_tokens, generic_cached)
        cache_write_tokens = cls._coerce_nonnegative_int(
            mapping.get("cache_write_tokens")
            or mapping.get("cache_creation_input_tokens")
        )

        if total_tokens == 0 and (input_tokens or output_tokens):
            total_tokens = input_tokens + output_tokens
        if not any(
            (
                input_tokens,
                output_tokens,
                total_tokens,
                cache_read_tokens,
                cache_write_tokens,
            )
        ):
            return None

        return TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
        )

    @classmethod
    def _usage_candidate(cls, event: dict) -> Tuple[Optional[TokenUsage], str]:
        """Return one usage record and its precedence class."""
        payload = event.get("payload")
        payload = payload if isinstance(payload, dict) else {}
        runtime_event = payload.get("event")
        runtime_event = runtime_event if isinstance(runtime_event, dict) else {}

        # Current Muse Code durable session schema. Each record represents one
        # completed model call, so all such records must be summed.
        if runtime_event.get("kind") == "model_completed":
            return cls._usage_from_mapping(runtime_event.get("usage")), "model"

        # Meta's /v1/responses object, plus common streamed response wrappers.
        response = event.get("response")
        if not isinstance(response, dict):
            response = payload.get("response")
        if not isinstance(response, dict):
            response = event
        response_usage = cls._usage_from_mapping(response.get("usage"))
        response_kind = str(event.get("object") or event.get("type") or "")
        if response_usage and (
            response.get("object") == "response"
            or response_kind.startswith("response.")
            or "response" in event
            or "response" in payload
        ):
            return response_usage, "response"

        # Forward-compatible Muse JSONL: accept usage on terminal or other
        # events if a future CLI version emits it directly.
        for container in (event, payload, runtime_event):
            usage = cls._usage_from_mapping(container.get("usage"))
            if usage:
                return usage, "generic"
        return None, ""

    @classmethod
    def _parse_token_usage(cls, output: str) -> Optional[TokenUsage]:
        """Aggregate model usage from Muse JSONL or a durable session log."""
        groups = {"model": [], "response": [], "generic": []}
        seen_ids = set()
        for event in cls._events(output):
            event_id = event.get("id")
            if event_id and event_id in seen_ids:
                continue
            usage, kind = cls._usage_candidate(event)
            if not usage:
                continue
            if event_id:
                seen_ids.add(event_id)
            groups[kind].append(usage)

        # Prefer durable per-call records, then API response objects, then a
        # possible run-level summary. This avoids double-counting projections
        # of the same call in richer future JSONL streams.
        records = groups["model"] or groups["response"] or groups["generic"]
        if not records:
            return None

        return TokenUsage(
            input_tokens=sum(item.input_tokens for item in records),
            output_tokens=sum(item.output_tokens for item in records),
            total_tokens=sum(item.total_tokens for item in records),
            cache_read_tokens=sum(item.cache_read_tokens for item in records),
            cache_write_tokens=sum(item.cache_write_tokens for item in records),
        )

    @classmethod
    def _parse_final_response(cls, output: str) -> Optional[str]:
        final_response = None
        deltas = []
        for event in cls._events(output):
            payload = event.get("payload")
            if not isinstance(payload, dict):
                continue
            if event.get("payload_type") == "run.output.delta":
                text = payload.get("text")
                if isinstance(text, str):
                    deltas.append(text)
            elif event.get("payload_type") == "run.terminal.completed":
                text = payload.get("text")
                if isinstance(text, str) and text:
                    final_response = text
        return final_response or ("".join(deltas) if deltas else None)

    @classmethod
    def _parse_model_name(cls, output: str) -> Optional[str]:
        for event in cls._events(output):
            payload = event.get("payload")
            if not isinstance(payload, dict):
                continue
            if event.get("payload_type") == "run.model.configured":
                model = payload.get("model_id") or payload.get("display_label")
                if isinstance(model, str) and model:
                    return model
            runtime_event = payload.get("event")
            if isinstance(runtime_event, dict) and runtime_event.get("kind") == "model_completed":
                model = runtime_event.get("model")
                if isinstance(model, str) and model:
                    return model
            record = payload.get("record")
            if isinstance(record, dict):
                model = record.get("model_id")
                if isinstance(model, str) and model:
                    return model
        return None

    @staticmethod
    def _default_data_root() -> Path:
        value = os.environ.get("XDG_DATA_HOME")
        return Path(value).expanduser() if value else Path.home() / ".local" / "share"

    @classmethod
    def _model_pricing(cls, model: str) -> Optional[Dict[str, float]]:
        """Read Meta's downloaded provider catalog, with exact fallbacks."""
        catalog_root = cls._default_data_root() / "muse" / "model-catalog"
        for path in sorted(catalog_root.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            rows = payload.get("rows") if isinstance(payload, dict) else None
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict) or row.get("model_id") != model:
                    continue
                cost = row.get("cost")
                if not isinstance(cost, dict):
                    continue
                try:
                    return {
                        "input": float(cost["input"]),
                        "output": float(cost["output"]),
                        "cached": float(cost["cached"]),
                    }
                except (KeyError, TypeError, ValueError, OverflowError):
                    continue
        return cls._FALLBACK_MODEL_PRICING.get(model)

    @classmethod
    def _calculate_cost(cls, usage: Optional[TokenUsage], model: str) -> float:
        if not usage:
            return 0.0
        pricing = cls._model_pricing(model)
        if not pricing:
            return 0.0
        cached_tokens = max(0, min(usage.cache_read_tokens, usage.input_tokens))
        uncached_tokens = max(0, usage.input_tokens - cached_tokens)
        return (
            uncached_tokens * pricing["input"]
            + cached_tokens * pricing["cached"]
            + usage.output_tokens * pricing["output"]
        ) / 1_000_000

    @classmethod
    def _link_static_muse_data(cls, isolated_data_home: Path) -> None:
        """Retain Muse's catalog/skills while isolating ephemeral sessions."""
        source = cls._default_data_root() / "muse"
        destination = isolated_data_home / "muse"
        destination.mkdir(parents=True, exist_ok=True)
        for name in ("model-catalog", "feature-config", "skills", "plugins"):
            source_path = source / name
            destination_path = destination / name
            if not source_path.exists() or destination_path.exists():
                continue
            try:
                destination_path.symlink_to(
                    source_path,
                    target_is_directory=source_path.is_dir(),
                )
            except OSError:
                # Usage logging still works if optional cached data cannot be
                # linked; Muse can recreate it inside the isolated data root.
                continue

    @staticmethod
    def _session_log(data_home: Path, session_id: str) -> Optional[Path]:
        sessions = data_home / "muse" / "sessions"
        if not sessions.is_dir():
            return None
        matches = list(sessions.glob(f"*/*/*/{session_id}/session.jsonl"))
        return matches[0] if matches else None

    def solve_task(self) -> SolverResult:
        config = self.load_config()
        if not config:
            return SolverResult(
                success=False,
                message="Could not load task configuration",
                duration_seconds=0.0,
            )

        prompt = self.get_task_prompt(config)
        start_time = time.time()
        workspace = os.getcwd()
        session_id = str(uuid.uuid4())
        stdout = ""
        stderr = ""
        returncode = None
        invocation_error = None
        timed_out = False
        token_usage = None
        logged_model = None

        if self.debug:
            print(f"Running Muse in {workspace} with model {self.model or 'default'}")

        with tempfile.TemporaryDirectory(prefix="gamedevbench-muse-") as data_root:
            data_home = Path(data_root)
            self._link_static_muse_data(data_home)
            environment = os.environ.copy()
            environment["XDG_DATA_HOME"] = str(data_home)
            cmd = [
                "muse",
                "exec",
                "--json",
                "--session-id",
                session_id,
                "--workspace",
                workspace,
                "--disable-approval",
                "--disable-sandbox",
            ]
            if self.model:
                cmd.extend(["--model", self.model])
            if self.effort:
                cmd.extend(["--reasoning-effort", self.effort])
            cmd.append(prompt)

            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                    cwd=workspace,
                    env=environment,
                )
                returncode = result.returncode
                stdout = result.stdout
                stderr = result.stderr
            except subprocess.TimeoutExpired as error:
                timed_out = True
                stdout = self._as_text(error.stdout)
                stderr = self._as_text(error.stderr)
            except FileNotFoundError as error:
                invocation_error = error
            except Exception as error:
                invocation_error = error

            session_log = self._session_log(data_home, session_id)
            if session_log:
                try:
                    durable_output = session_log.read_text(
                        encoding="utf-8",
                        errors="replace",
                    )
                except OSError:
                    durable_output = ""
                token_usage = self._parse_token_usage(durable_output)
                logged_model = self._parse_model_name(durable_output)

        duration = time.time() - start_time
        model_used = (
            self._parse_model_name(stdout)
            or logged_model
            or self.model
            or "muse"
        )
        token_usage = token_usage or self._parse_token_usage(stdout)
        cost_usd = self._calculate_cost(token_usage, model_used)

        if invocation_error:
            if isinstance(invocation_error, FileNotFoundError):
                message = "Muse CLI not found. Install it from https://dev.meta.ai/install.sh"
            else:
                message = f"Error invoking Muse: {invocation_error}"
            return SolverResult(
                success=False,
                message=message,
                duration_seconds=duration,
                stdout=stdout,
                stderr=stderr,
                is_rate_limited=self.is_rate_limit_error(message),
                token_usage=token_usage,
                model=model_used,
                cost_usd=cost_usd,
            )

        if timed_out:
            message = f"Muse execution timed out after {self.timeout_seconds}s"
            return SolverResult(
                success=False,
                message=message,
                duration_seconds=duration,
                stdout=stdout,
                stderr=stderr,
                token_usage=token_usage,
                model=model_used,
                cost_usd=cost_usd,
            )

        final_response = self._parse_final_response(stdout)
        if returncode == 0:
            message = final_response or "Muse completed without a final response."
        else:
            message = f"Muse command failed (exit code {returncode})"
            if stderr.strip():
                message += f"\nSTDERR: {stderr.strip()}"
            if final_response:
                message += f"\nFinal response: {final_response}"

        if self.debug and token_usage:
            print(
                "Tokens: "
                f"input={token_usage.input_tokens}, "
                f"output={token_usage.output_tokens}, "
                f"cached={token_usage.cache_read_tokens}, "
                f"total={token_usage.total_tokens}"
            )
            print(f"Cost: ${cost_usd:.6f}")

        return SolverResult(
            success=returncode == 0,
            message=message,
            duration_seconds=duration,
            stdout=stdout,
            stderr=stderr,
            is_rate_limited=self.is_rate_limit_error(message),
            token_usage=token_usage,
            model=model_used,
            cost_usd=cost_usd,
        )
