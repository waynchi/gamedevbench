#!/usr/bin/env python3
"""
Google Gemini solver for gamedev benchmark tasks.
Uses Gemini CLI (https://github.com/google-gemini/gemini-cli) for task completion.
"""

import asyncio
import json
import time
import os
from typing import Any, Optional

from gamedevbench.src.base_solver import BaseSolver
from gamedevbench.src.utils.data_types import SolverResult, TokenUsage


class GeminiSolver(BaseSolver):
    """Solver that uses Google Gemini CLI to complete game development tasks."""

    # Solver capabilities (required by BaseSolver)
    SUPPORTS_MCP = True
    SUPPORTS_SYSTEM_PROMPT = False

    def __init__(
        self,
        timeout_seconds: int = 600,
        debug: bool = False,
        use_yolo: bool = True,  # Auto-approve all actions
        model: Optional[str] = None,  # Model name to use with --model flag
        use_mcp: bool = False,
        use_runtime_video: bool = False,
    ):
        """Initialize the Gemini solver.

        Args:
            timeout_seconds: Maximum time to wait for completion
            debug: Enable verbose output
            use_yolo: Use --yolo flag to auto-approve all actions
            model: Model name to pass via --model flag (optional)
            use_mcp: Whether to use MCP tools (enables gamedevbench-mcp server via gemini mcp enable/disable)
            use_runtime_video: Whether to append Godot runtime video instructions to prompts
        """
        # Call parent constructor (handles MCP validation)
        super().__init__(timeout_seconds, debug, use_mcp, use_runtime_video)

        # Gemini-specific parameters
        self.use_yolo = use_yolo
        self.model = model

    @staticmethod
    def _coerce_int(value: Any) -> int:
        """Best-effort conversion for token counters from CLI JSON output."""
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

    def _extract_usage_from_mapping(self, payload: Optional[dict]) -> Optional[TokenUsage]:
        """Extract token usage from one Gemini JSON object."""
        if not isinstance(payload, dict):
            return None

        stats = payload.get("stats")
        if isinstance(stats, dict):
            model_stats = None
            models = stats.get("models")
            if isinstance(models, dict) and models:
                preferred_model = self.model.lower() if self.model else None
                if preferred_model:
                    for model_name, model_payload in models.items():
                        if preferred_model in model_name.lower():
                            model_stats = model_payload
                            break
                if model_stats is None:
                    model_stats = next(iter(models.values()))

            usage_stats = model_stats if isinstance(model_stats, dict) else stats
            input_tokens = self._coerce_int(usage_stats.get("input_tokens"))
            output_tokens = self._coerce_int(usage_stats.get("output_tokens"))
            total_tokens = self._coerce_int(usage_stats.get("total_tokens"))
            cache_read_tokens = self._coerce_int(
                usage_stats.get("cached") or usage_stats.get("cached_tokens")
            )

            if total_tokens == 0 and (input_tokens > 0 or output_tokens > 0):
                total_tokens = input_tokens + output_tokens

            if total_tokens > 0 or cache_read_tokens > 0:
                return TokenUsage(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=total_tokens,
                    cache_read_tokens=cache_read_tokens,
                    cache_write_tokens=0,
                )

        nested_usage = None
        for key in ("usage", "usageMetadata"):
            value = payload.get(key)
            if isinstance(value, dict):
                nested_usage = value
                break

        if not nested_usage:
            for key in ("response", "payload", "result"):
                value = payload.get(key)
                if isinstance(value, dict):
                    nested_usage = self._extract_usage_from_mapping(value)
                    if nested_usage:
                        return nested_usage

        usage = nested_usage if isinstance(nested_usage, dict) else payload

        input_tokens = self._coerce_int(
            usage.get("input_tokens")
            or usage.get("inputTokens")
            or usage.get("prompt_tokens")
            or usage.get("promptTokenCount")
        )
        output_tokens = self._coerce_int(
            usage.get("output_tokens")
            or usage.get("outputTokens")
            or usage.get("completion_tokens")
            or usage.get("candidatesTokenCount")
        )
        total_tokens = self._coerce_int(
            usage.get("total_tokens")
            or usage.get("totalTokens")
            or usage.get("totalTokenCount")
        )
        cache_read_tokens = self._coerce_int(
            usage.get("cached_tokens")
            or usage.get("cachedTokens")
            or usage.get("cache_read_input_tokens")
            or usage.get("cachedContentTokenCount")
        )

        if total_tokens == 0 and (input_tokens > 0 or output_tokens > 0):
            total_tokens = input_tokens + output_tokens

        if total_tokens == 0 and cache_read_tokens == 0:
            return None

        return TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=0,
        )

    @staticmethod
    def is_rate_limit_error(error_message: str) -> bool:
        """Check if the error message indicates API rate limit or quota exceeded."""
        error_lower = error_message.lower()
        rate_limit_keywords = [
            "rate limit",
            "rate_limit",
            "ratelimit",
            "quota exceeded",
            "quota_exceeded",
            "429",
            "too many requests",
            "resource exhausted",
            "resource_exhausted",
        ]
        return any(keyword in error_lower for keyword in rate_limit_keywords)

    async def _ensure_mcp_server_configured(self) -> bool:
        """Ensure the gamedevbench-mcp server is configured in Gemini CLI.

        Checks if server exists, adds it if missing.

        Returns:
            True if server is configured, False otherwise
        """
        # Check if server is already configured by listing MCP servers
        try:
            proc = await asyncio.create_subprocess_exec(
                "gemini", "mcp", "list",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, _ = await proc.communicate()
            stdout = (stdout_bytes or b"").decode(errors="ignore")

            # If gamedevbench-mcp is in the list, it's already configured
            if "gamedevbench-mcp" in stdout:
                if self.debug:
                    print("MCP server gamedevbench-mcp is already configured")
                return True

            # Server not found, add it
            if self.debug:
                print("Adding MCP server gamedevbench-mcp...")

            proc = await asyncio.create_subprocess_exec(
                "gemini", "mcp", "add", "gamedevbench-mcp", "uv", "run", "gamedevbench-mcp",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()

            if proc.returncode == 0:
                if self.debug:
                    print("MCP server gamedevbench-mcp added successfully")
                return True
            else:
                if self.debug:
                    print(f"Failed to add MCP server (exit code: {proc.returncode})")
                return False

        except Exception as e:
            if self.debug:
                print(f"Error configuring MCP server: {e}")
            return False

    async def solve_task_async(self) -> SolverResult:
        """Solve the task in the current directory using Gemini CLI."""
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
            print("SENDING PROMPT TO GEMINI CLI:")
            print("=" * 60)
            print(prompt)
            print("=" * 60)

        # Ensure MCP server is configured if requested
        if self.use_mcp:
            mcp_configured = await self._ensure_mcp_server_configured()
            if not mcp_configured and self.debug:
                print("Warning: Could not configure MCP server. Continuing without screenshot capability.")

        try:
            # Build gemini command
            cmd = ["gemini"]

            if self.use_yolo:
                cmd.append("--yolo")

            if self.model:
                cmd.extend(["--model", self.model])

            if os.environ.get("GAMEDEVBENCH_CONFINED") == "1":
                cmd.extend(
                    [
                        "--admin-policy",
                        "/home/sandbox/.gemini/policies/gamedevbench.toml",
                    ]
                )

            cmd.extend(["--output-format", "stream-json"])

            cmd.extend(["-p", prompt])

            if self.debug:
                print(f"\nRunning: {' '.join(cmd[:3])} -p \"...\"")
                print("\nGEMINI TRAJECTORY:")
                print("=" * 60)

            # Run Gemini CLI
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=os.getcwd(),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=self.timeout_seconds,
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                duration = time.time() - start_time
                return SolverResult(
                    success=False,
                    message=f"Gemini CLI timed out after {self.timeout_seconds}s",
                    duration_seconds=duration,
                )

            duration = time.time() - start_time
            stdout = (stdout_bytes or b"").decode(errors="ignore")
            stderr = (stderr_bytes or b"").decode(errors="ignore")

            if self.debug:
                if stdout:
                    print(stdout)
                if stderr:
                    print(f"\nStderr: {stderr}")
                print(f"\n\nDuration: {duration:.2f} seconds")
                print(f"Exit code: {proc.returncode}")
                print("=" * 60)

            # Parse token usage and model info if available (from JSON output)
            token_usage = self._parse_token_usage(stdout)
            model_used = self._parse_model_name(stdout) or self.model or "gemini"

            # Calculate cost
            cost_usd = 0.0
            if token_usage:
                cost_usd = token_usage.calculate_cost(model_used)

            if self.debug and token_usage:
                print(f"Tokens: input={token_usage.input_tokens}, output={token_usage.output_tokens}, total={token_usage.total_tokens}")
                print(f"Cost: ${cost_usd:.4f}")

            # Check for errors in stderr
            combined_output = stdout if not stderr else f"{stdout}\n{stderr}"

            return SolverResult(
                success=proc.returncode == 0,
                message="Task completed" if proc.returncode == 0 else f"Gemini CLI exited with code {proc.returncode}",
                duration_seconds=duration,
                stdout=stdout,
                stderr=stderr,
                token_usage=token_usage,
                model=model_used,
                cost_usd=cost_usd,
            )

        except FileNotFoundError:
            return SolverResult(
                success=False,
                message="Gemini CLI not found. Install from: https://github.com/google-gemini/gemini-cli",
                duration_seconds=0.0,
            )
        except Exception as e:
            duration = time.time() - start_time
            error_msg = str(e)
            is_rate_limited = self.is_rate_limit_error(error_msg)

            if self.debug:
                print(f"\nERROR INVOKING GEMINI CLI: {error_msg}")
                if is_rate_limited:
                    print("⚠️  DETECTED RATE LIMIT/QUOTA ERROR")
                print("=" * 60)

            return SolverResult(
                success=False,
                message=f"Error invoking Gemini CLI: {error_msg}",
                duration_seconds=duration,
                is_rate_limited=is_rate_limited,
            )

    def _parse_token_usage(self, output: str) -> Optional[TokenUsage]:
        """Parse JSON output to extract token usage information.

        Gemini CLI with --output-format stream-json outputs events like:
        {"type": "usage", "input_tokens": 123, "output_tokens": 456}
        """
        total_input = 0
        total_output = 0
        total_cached = 0
        total_tokens = 0

        for line in output.strip().split("\n"):
            if not line:
                continue
            try:
                event = json.loads(line)
                usage = self._extract_usage_from_mapping(event)
                if usage:
                    total_input += usage.input_tokens
                    total_output += usage.output_tokens
                    total_cached += usage.cache_read_tokens
                    total_tokens += usage.total_tokens or (
                        usage.input_tokens + usage.output_tokens
                    )

            except json.JSONDecodeError:
                # Not a JSON line, skip
                continue

        if total_input > 0 or total_output > 0:
            return TokenUsage(
                input_tokens=total_input,
                output_tokens=total_output,
                total_tokens=total_tokens or (total_input + total_output),
                cache_read_tokens=total_cached,
                cache_write_tokens=0,
            )
        return None

    def _parse_model_name(self, output: str) -> Optional[str]:
        """Parse JSON output to extract the model name."""
        for line in output.strip().split("\n"):
            if not line:
                continue
            try:
                event = json.loads(line)
                for candidate in (
                    event.get("model"),
                    event.get("modelName"),
                    event.get("metadata", {}).get("model") if isinstance(event.get("metadata"), dict) else None,
                    event.get("payload", {}).get("model") if isinstance(event.get("payload"), dict) else None,
                ):
                    if candidate:
                        return candidate
            except json.JSONDecodeError:
                continue
        return None

    def solve_task(self) -> SolverResult:
        """Synchronous wrapper for async solve_task_async."""
        return asyncio.run(self.solve_task_async())


def main():
    """Main function for testing the solver."""
    solver = GeminiSolver(debug=True)
    result = solver.solve_task()
    print("\n" + "=" * 60)
    print("RESULT:")
    print("=" * 60)
    print(f"Success: {result.success}")
    print(f"Message: {result.message}")
    print(f"Duration: {result.duration_seconds:.2f}s")
    if result.token_usage:
        print(f"Tokens: {result.token_usage.total_tokens}")
        print(f"Cost: ${result.cost_usd:.4f}")


if __name__ == "__main__":
    main()
