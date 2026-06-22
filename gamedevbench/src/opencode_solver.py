#!/usr/bin/env python3
"""
OpenCode solver for gamedev benchmark tasks.
Uses OpenCode CLI in non-interactive mode.
"""

import json
import os
import subprocess
import time
from typing import Any, Optional

from gamedevbench.src.base_solver import BaseSolver
from gamedevbench.src.utils.data_types import SolverResult, TokenUsage


class OpenCodeSolver(BaseSolver):
    """Solver that uses OpenCode CLI to complete game development tasks."""

    SUPPORTS_MCP = False
    SUPPORTS_SYSTEM_PROMPT = False

    def __init__(
        self,
        timeout_seconds: int = 600,
        debug: bool = False,
        model: Optional[str] = None,
        agent: str = "build",
        use_runtime_video: bool = False,
    ):
        super().__init__(timeout_seconds, debug, False, use_runtime_video)
        self.model = model
        self.agent = agent

    @staticmethod
    def _coerce_int(value: Any) -> int:
        if value is None:
            return 0
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, str):
            try:
                return int(float(value))
            except ValueError:
                return 0
        return 0

    @staticmethod
    def is_rate_limit_error(error_message: str) -> bool:
        error_lower = error_message.lower()
        rate_limit_keywords = [
            "rate limit",
            "rate_limit",
            "ratelimit",
            "quota exceeded",
            "quota_exceeded",
            "429",
            "too many requests",
            "usage limit",
            "credit balance",
        ]
        return any(keyword in error_lower for keyword in rate_limit_keywords)

    def solve_task(self) -> SolverResult:
        """Solve the task using OpenCode CLI."""
        config = self.load_config()
        if not config:
            return SolverResult(
                success=False,
                message="Could not load task configuration",
                duration_seconds=0.0,
            )

        start_time = time.time()
        prompt = self.get_task_prompt(config)

        if self.debug:
            print("=" * 60)
            print("SENDING PROMPT TO OPENCODE:")
            print("=" * 60)
            print(prompt)
            print("=" * 60)

        try:
            cmd = [
                "opencode",
                "run",
                "--format",
                "json",
                "--dangerously-skip-permissions",
                "--dir",
                str(os.getcwd()),
            ]

            if self.agent:
                cmd.extend(["--agent", self.agent])
            if self.model:
                cmd.extend(["--model", self.model])

            cmd.append(prompt)

            if self.debug:
                cmd_str = " ".join([c if " " not in c else f'"{c}"' for c in cmd[:-1]])
                print(f"Running: {cmd_str} \"...\"")
                print("\nOPENCODE TRAJECTORY:")
                print("=" * 60)

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                cwd=os.getcwd(),
            )

            duration = time.time() - start_time
            stdout = result.stdout
            stderr = result.stderr

            if self.debug:
                self._print_trajectory(stdout)
                print(f"\n\nDuration: {duration:.2f} seconds")
                print(f"Exit code: {result.returncode}")
                if stderr:
                    print(f"Stderr: {stderr[:500]}")
                print("=" * 60)

            final_response = self._parse_final_response(stdout)
            token_usage = self._parse_token_usage(stdout)
            model_used = self._parse_model_name(stdout) or self.model or "opencode"
            cost_usd = token_usage.calculate_cost(model_used) if token_usage else 0.0

            if result.returncode != 0:
                message = f"OpenCode command failed (exit code {result.returncode})"
                if stderr and stderr.strip():
                    message += f"\nSTDERR: {stderr.strip()}"
                if final_response:
                    message += f"\nFinal response: {final_response}"
            else:
                message = final_response or "Task completed"

            return SolverResult(
                success=result.returncode == 0,
                message=message,
                duration_seconds=duration,
                stdout=stdout,
                stderr=stderr,
                token_usage=token_usage,
                model=model_used,
                cost_usd=cost_usd,
                is_rate_limited=self.is_rate_limit_error(stdout + "\n" + stderr),
            )

        except subprocess.TimeoutExpired:
            duration = time.time() - start_time
            return SolverResult(
                success=False,
                message=f"OpenCode execution timed out after {self.timeout_seconds}s",
                duration_seconds=duration,
            )
        except FileNotFoundError:
            return SolverResult(
                success=False,
                message="OpenCode CLI not found. Install with: npm install -g opencode-ai",
                duration_seconds=0.0,
            )
        except Exception as e:
            duration = time.time() - start_time
            error_msg = str(e)
            return SolverResult(
                success=False,
                message=f"Error invoking OpenCode: {error_msg}",
                duration_seconds=duration,
                is_rate_limited=self.is_rate_limit_error(error_msg),
            )

    def _print_trajectory(self, output: str):
        for event in self._iter_json_events(output):
            event_type = str(event.get("type") or event.get("event") or "")
            if event_type:
                print(f"[{event_type}]")

    def _iter_json_events(self, output: str):
        for line in output.strip().splitlines():
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                yield event

    def _parse_final_response(self, output: str) -> Optional[str]:
        final_response = None
        for event in self._iter_json_events(output):
            for key in ("message", "text", "content", "result", "response"):
                value = event.get(key)
                if isinstance(value, str) and value.strip():
                    final_response = value.strip()

            data = event.get("data") or event.get("part")
            if isinstance(data, dict):
                for key in ("message", "text", "content"):
                    value = data.get(key)
                    if isinstance(value, str) and value.strip():
                        final_response = value.strip()
        return final_response

    def _parse_model_name(self, output: str) -> Optional[str]:
        for event in self._iter_json_events(output):
            for key in ("model", "modelID", "modelId"):
                value = event.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()

            for key in ("session", "message", "data", "metadata"):
                nested = event.get(key)
                if isinstance(nested, dict):
                    for model_key in ("model", "modelID", "modelId"):
                        value = nested.get(model_key)
                        if isinstance(value, str) and value.strip():
                            return value.strip()
        return None

    def _parse_token_usage(self, output: str) -> Optional[TokenUsage]:
        input_tokens = 0
        output_tokens = 0
        cache_read_tokens = 0

        for event in self._iter_json_events(output):
            usage = event.get("usage")
            if not isinstance(usage, dict):
                for key in ("data", "message", "metadata"):
                    nested = event.get(key)
                    if isinstance(nested, dict) and isinstance(nested.get("usage"), dict):
                        usage = nested["usage"]
                        break

            if isinstance(usage, dict):
                input_tokens += self._coerce_int(
                    usage.get("input")
                    or usage.get("input_tokens")
                    or usage.get("prompt_tokens")
                )
                output_tokens += self._coerce_int(
                    usage.get("output")
                    or usage.get("output_tokens")
                    or usage.get("completion_tokens")
                )
                cache_read_tokens += self._coerce_int(
                    usage.get("cache_read")
                    or usage.get("cache_read_input_tokens")
                    or usage.get("cached_tokens")
                )

            tokens = event.get("tokens")
            if not isinstance(tokens, dict):
                part = event.get("part")
                if isinstance(part, dict) and isinstance(part.get("tokens"), dict):
                    tokens = part["tokens"]

            if isinstance(tokens, dict):
                input_tokens += self._coerce_int(tokens.get("input"))
                output_tokens += self._coerce_int(tokens.get("output"))
                cache = tokens.get("cache")
                if isinstance(cache, dict):
                    cache_read_tokens += self._coerce_int(cache.get("read"))

        if input_tokens == 0 and output_tokens == 0 and cache_read_tokens == 0:
            return None

        return TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=0,
        )
