#!/usr/bin/env python3
"""Oh My OpenAgent solver using an isolated OpenCode configuration."""

import json
import os
import shutil
from pathlib import Path
from typing import Optional

from gamedevbench.src.opencode_solver import OpenCodeSolver
from gamedevbench.src.utils.constants import PROJECT_ROOT
from gamedevbench.src.utils.data_types import SolverResult, TokenUsage
from gamedevbench.src.utils.processes import run_cli_command


DEFAULT_XDG_CONFIG_HOME = PROJECT_ROOT / ".benchmark-config" / "omo"


class OmoSolver(OpenCodeSolver):
    """Run OMO's Sisyphus orchestrator without touching the user's OpenCode config."""

    def solve_task(self) -> SolverResult:
        config_dir = self._xdg_config_home() / "opencode"
        if not (config_dir / "opencode.json").is_file():
            return SolverResult(False, f"OMO config is not prepared: {config_dir}", 0.0)
        if not any(
            (config_dir / name).is_file()
            for name in ("oh-my-openagent.jsonc", "oh-my-opencode.jsonc")
        ):
            return SolverResult(False, f"OMO model config is missing: {config_dir}", 0.0)
        result = super().solve_task()
        if "not found. Falling back to default agent" in (result.stderr or ""):
            result.success = False
            result.message = "OMO primary agent was not found; OpenCode used its default agent"
        self._add_subagent_usage(result)
        return result

    def _xdg_config_home(self) -> Path:
        override = os.environ.get("GAMEDEVBENCH_OMO_XDG_CONFIG_HOME")
        return Path(override) if override else DEFAULT_XDG_CONFIG_HOME

    def _agent_name(self) -> Optional[str]:
        return "Sisyphus - ultraworker"

    def _command_environment(self) -> Optional[dict[str, str]]:
        xdg_config_home = self._xdg_config_home()
        env = os.environ.copy()
        env["XDG_CONFIG_HOME"] = str(xdg_config_home)
        env["OPENCODE_CONFIG_DIR"] = str(xdg_config_home / "opencode")
        env["OMO_SEND_ANONYMOUS_TELEMETRY"] = "0"
        env["OMO_DISABLE_POSTHOG"] = "1"
        env["OPENCODE_DISABLE_CLAUDE_CODE"] = "true"
        env["OPENCODE_DISABLE_CLAUDE_CODE_PROMPT"] = "true"
        env["OPENCODE_DISABLE_CLAUDE_CODE_SKILLS"] = "true"
        return env

    def _add_subagent_usage(self, result: SolverResult) -> None:
        session_ids = self._subagent_session_ids(result.stdout)
        executable = shutil.which("opencode")
        if not session_ids or not executable:
            return

        aggregate = result.token_usage or TokenUsage()
        for session_id in session_ids:
            try:
                exported = run_cli_command(
                    [executable, "export", session_id],
                    timeout_seconds=30,
                    cwd=os.getcwd(),
                    env=self._command_environment(),
                )
            except Exception:
                continue
            usage = self._parse_exported_usage(exported.stdout)
            if not usage:
                continue
            aggregate.input_tokens += usage.input_tokens
            aggregate.output_tokens += usage.output_tokens
            aggregate.total_tokens += usage.total_tokens
            aggregate.cache_read_tokens += usage.cache_read_tokens
            aggregate.cache_write_tokens += usage.cache_write_tokens

        result.token_usage = aggregate
        result.calculate_cost()

    @staticmethod
    def _subagent_session_ids(output: str) -> set[str]:
        session_ids = set()
        for line in output.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            part = event.get("part", {})
            if event.get("type") != "tool_use" or part.get("tool") not in {
                "task",
                "call_omo_agent",
            }:
                continue
            metadata = part.get("state", {}).get("metadata", {})
            session_id = metadata.get("sessionId") or metadata.get("taskId")
            if session_id:
                session_ids.add(session_id)
        return session_ids

    @staticmethod
    def _parse_exported_usage(output: str) -> Optional[TokenUsage]:
        try:
            payload = json.loads(output)
        except json.JSONDecodeError:
            return None
        tokens = payload.get("info", {}).get("tokens", {})
        input_tokens = tokens.get("input", 0) or 0
        output_tokens = (tokens.get("output", 0) or 0) + (
            tokens.get("reasoning", 0) or 0
        )
        cache = tokens.get("cache", {})
        if not input_tokens and not output_tokens:
            return None
        return TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            cache_read_tokens=cache.get("read", 0) or 0,
            cache_write_tokens=cache.get("write", 0) or 0,
        )
