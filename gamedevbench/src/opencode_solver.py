#!/usr/bin/env python3
"""OpenCode CLI solver for GameDevBench tasks."""

import json
import os
import shutil
import subprocess
import time
from typing import Optional

from gamedevbench.src.base_solver import BaseSolver
from gamedevbench.src.utils.data_types import SolverResult, TokenUsage
from gamedevbench.src.utils.processes import run_cli_command


class OpenCodeSolver(BaseSolver):
    """Run tasks through OpenCode while preserving its default agent and tools."""

    SUPPORTS_MCP = False
    SUPPORTS_SYSTEM_PROMPT = False

    def __init__(
        self,
        timeout_seconds: int = 600,
        debug: bool = False,
        model: Optional[str] = None,
        use_runtime_video: bool = False,
    ):
        super().__init__(timeout_seconds, debug, False, use_runtime_video)
        self.model = model

    @staticmethod
    def is_rate_limit_error(error_message: str) -> bool:
        error_lower = error_message.lower()
        return any(
            keyword in error_lower
            for keyword in (
                "rate limit",
                "rate_limit",
                "quota exceeded",
                "too many requests",
                "429",
            )
        )

    def solve_task(self) -> SolverResult:
        config = self.load_config()
        if not config:
            return SolverResult(False, "Could not load task configuration", 0.0)

        prompt = self.get_task_prompt(config)
        executable = shutil.which("opencode")
        if not executable:
            return SolverResult(
                False,
                "OpenCode CLI not found. Install from: https://opencode.ai/docs/",
                0.0,
            )

        cmd = [
            executable,
            "run",
            "--format",
            "json",
            "--dangerously-skip-permissions",
            "--dir",
            os.getcwd(),
        ]
        if self.model:
            cmd.extend(["--model", self.model])
        agent = self._agent_name()
        if agent:
            cmd.extend(["--agent", agent])
        cmd.append(prompt)

        start_time = time.time()
        try:
            result = run_cli_command(
                cmd,
                timeout_seconds=self.timeout_seconds,
                cwd=os.getcwd(),
                env=self._command_environment(),
            )
            duration = time.time() - start_time
            parsed = self._parse_output(result.stdout)
            error_message = parsed["error"]
            success = result.returncode == 0 and not error_message
            model_used = parsed["model"] or self.model or "opencode-default"
            token_usage = parsed["token_usage"]
            cost_usd = token_usage.calculate_cost(model_used) if token_usage else 0.0

            if success:
                message = parsed["response"] or "Task completed"
            else:
                details = error_message or result.stderr.strip()
                message = f"OpenCode CLI failed (exit code {result.returncode})"
                if details:
                    message += f": {details}"

            combined_error = "\n".join(
                part for part in (error_message, result.stderr) if part
            )

            if self.debug:
                print(result.stdout)
                if result.stderr:
                    print(result.stderr)

            return SolverResult(
                success=success,
                message=message,
                duration_seconds=duration,
                stdout=result.stdout,
                stderr=result.stderr,
                is_rate_limited=self.is_rate_limit_error(combined_error),
                token_usage=token_usage,
                model=model_used,
                cost_usd=cost_usd,
            )
        except subprocess.TimeoutExpired:
            return SolverResult(
                False,
                f"OpenCode CLI timed out after {self.timeout_seconds}s",
                time.time() - start_time,
            )
        except FileNotFoundError:
            return SolverResult(
                False,
                "OpenCode CLI not found. Install from: https://opencode.ai/docs/",
                0.0,
            )
        except Exception as exc:
            error_message = str(exc)
            return SolverResult(
                False,
                f"Error invoking OpenCode: {error_message}",
                time.time() - start_time,
                is_rate_limited=self.is_rate_limit_error(error_message),
            )

    def _agent_name(self) -> Optional[str]:
        return None

    def _command_environment(self) -> Optional[dict[str, str]]:
        return None

    @staticmethod
    def _parse_output(output: str) -> dict:
        input_tokens = 0
        output_tokens = 0
        cache_read_tokens = 0
        cache_write_tokens = 0
        model = ""
        response_parts = []
        error_message = ""

        for line in output.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            event_type = event.get("type")
            part = event.get("part", {})
            model = (
                event.get("modelID")
                or event.get("model")
                or part.get("modelID")
                or part.get("model")
                or model
            )
            if event_type == "text" and part.get("text"):
                response_parts.append(part["text"])
            elif event_type == "step_finish":
                tokens = part.get("tokens", {})
                cache = tokens.get("cache", {})
                input_tokens += tokens.get("input", 0) or 0
                output_tokens += (tokens.get("output", 0) or 0) + (
                    tokens.get("reasoning", 0) or 0
                )
                cache_read_tokens += cache.get("read", 0) or 0
                cache_write_tokens += cache.get("write", 0) or 0
            elif event_type == "error":
                error = event.get("error", {})
                data = error.get("data", {}) if isinstance(error, dict) else {}
                error_message = data.get("message") or str(error)

        token_usage = None
        if input_tokens or output_tokens:
            token_usage = TokenUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
                cache_read_tokens=cache_read_tokens,
                cache_write_tokens=cache_write_tokens,
            )

        return {
            "response": "\n".join(response_parts),
            "error": error_message,
            "token_usage": token_usage,
            "model": model,
        }


if __name__ == "__main__":
    print(OpenCodeSolver(debug=True).solve_task())
