#!/usr/bin/env python3
"""Meta Muse CLI solver for GameDevBench."""

import json
import os
import subprocess
import time
from typing import Optional

from gamedevbench.src.base_solver import BaseSolver
from gamedevbench.src.utils.data_types import SolverResult


class MuseSolver(BaseSolver):
    """Run a task with the Muse coding agent in headless JSON mode."""

    SUPPORTS_MCP = False
    SUPPORTS_SYSTEM_PROMPT = False
    SUPPORTS_EFFORT = True

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
        return any(
            marker in error_lower
            for marker in (
                "rate limit",
                "rate_limit",
                "ratelimit",
                "quota exceeded",
                "too many requests",
                "429",
            )
        )

    @staticmethod
    def _events(output: str):
        for line in output.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                yield event

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
            if event.get("payload_type") != "run.model.configured":
                continue
            payload = event.get("payload")
            if isinstance(payload, dict):
                model = payload.get("model_id") or payload.get("display_label")
                if isinstance(model, str) and model:
                    return model
        return None

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
        cmd = [
            "muse",
            "exec",
            "--json",
            "--workspace",
            workspace,
            "--disable-approval",
            "--disable-sandbox",
            "--no-session-log",
        ]
        if self.model:
            cmd.extend(["--model", self.model])
        if self.effort:
            cmd.extend(["--reasoning-effort", self.effort])
        cmd.append(prompt)

        if self.debug:
            print(f"Running Muse in {workspace} with model {self.model or 'default'}")

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                cwd=workspace,
            )
        except subprocess.TimeoutExpired:
            duration = time.time() - start_time
            return SolverResult(
                success=False,
                message=f"Muse execution timed out after {self.timeout_seconds}s",
                duration_seconds=duration,
                model=self.model or "muse",
            )
        except FileNotFoundError:
            return SolverResult(
                success=False,
                message="Muse CLI not found. Install it from https://dev.meta.ai/install.sh",
                duration_seconds=0.0,
                model=self.model or "muse",
            )
        except Exception as error:
            duration = time.time() - start_time
            message = str(error)
            return SolverResult(
                success=False,
                message=f"Error invoking Muse: {message}",
                duration_seconds=duration,
                is_rate_limited=self.is_rate_limit_error(message),
                model=self.model or "muse",
            )

        duration = time.time() - start_time
        final_response = self._parse_final_response(result.stdout)
        model_used = self._parse_model_name(result.stdout) or self.model or "muse"

        if result.returncode == 0:
            message = final_response or "Muse completed without a final response."
        else:
            message = f"Muse command failed (exit code {result.returncode})"
            if result.stderr.strip():
                message += f"\nSTDERR: {result.stderr.strip()}"
            if final_response:
                message += f"\nFinal response: {final_response}"

        return SolverResult(
            success=result.returncode == 0,
            message=message,
            duration_seconds=duration,
            stdout=result.stdout,
            stderr=result.stderr,
            is_rate_limited=self.is_rate_limit_error(message),
            model=model_used,
        )
