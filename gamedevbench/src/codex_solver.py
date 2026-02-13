#!/usr/bin/env python3
"""
OpenAI Codex solver for gamedev benchmark tasks.
Uses Codex CLI with MCP server for Godot screenshots.
"""

import json
import time
import os
import subprocess
from pathlib import Path
from typing import Optional

from gamedevbench.src.base_solver import BaseSolver
from gamedevbench.src.utils.data_types import SolverResult, TokenUsage


class CodexSolver(BaseSolver):
    """Solver that uses OpenAI Codex CLI to complete game development tasks."""

    # Solver capabilities (required by BaseSolver)
    SUPPORTS_MCP = True
    SUPPORTS_SYSTEM_PROMPT = False  # Codex embeds context in main prompt

    def __init__(
        self,
        timeout_seconds: int = 600,
        debug: bool = False,
        use_mcp: bool = False,
        model: Optional[str] = None,
        approval_policy: str = "never",      # never | auto-edit | full-auto
        sandbox: str = "workspace-write",    # read-only | workspace-write | danger-full-access
        use_runtime_video: bool = False,
    ):
        # Call parent constructor (handles MCP validation)
        super().__init__(timeout_seconds, debug, use_mcp, use_runtime_video)

        # Codex-specific parameters
        self.model = model
        self.approval_policy = approval_policy
        self.sandbox = sandbox

        # Only configure MCP if enabled
        if use_mcp:
            self._ensure_mcp_config()

    def _ensure_mcp_config(self):
        """Ensure ~/.codex/config.toml contains godot-screenshot MCP server config."""
        config_dir = Path.home() / ".codex"
        config_file = config_dir / "config.toml"

        mcp_config = '''
[mcp_servers.godot-screenshot]
command = "uv"
args = ["run", "gamedevbench-mcp"]
'''

        config_dir.mkdir(parents=True, exist_ok=True)

        if config_file.exists():
            content = config_file.read_text()
            if "godot-screenshot" not in content:
                # Append MCP config
                with open(config_file, 'a') as f:
                    f.write("\n" + mcp_config)
                if self.debug:
                    print(f"Added godot-screenshot MCP config to {config_file}")
        else:
            # Create new config file
            config_file.write_text(mcp_config.strip())
            if self.debug:
                print(f"Created Codex config at {config_file}")

    @staticmethod
    def is_rate_limit_error(error_message: str) -> bool:
        """Check if the error message indicates API rate limit."""
        error_lower = error_message.lower()
        rate_limit_keywords = [
            "rate limit", "rate_limit", "ratelimit",
            "quota exceeded", "429", "too many requests",
        ]
        return any(keyword in error_lower for keyword in rate_limit_keywords)

    def solve_task(self) -> SolverResult:
        """Solve the task using Codex CLI."""
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
            print("SENDING PROMPT TO CODEX CLI:")
            print("=" * 60)
            print(prompt)
            print("=" * 60)

        try:
            # Build codex exec command
            cmd = [
                "codex",
                "--model", 
                self.model,
                "exec",
                "--skip-git-repo-check",
                "--yolo",
                "-s", 
                "danger-full-access",
                "-C", 
                str(os.getcwd()),
                prompt,
            ]

            if self.debug:
                cmd_str = " ".join([c if " " not in c else f'"{c}"' for c in cmd[:-1]])
                print(f"Running: {cmd_str} \"...\"")
                print("\nCODEX TRAJECTORY:")
                print("=" * 60)

            # Run Codex
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
                # Parse and print key events
                self._print_trajectory(stdout)
                print(f"\n\nDuration: {duration:.2f} seconds")
                print(f"Exit code: {result.returncode}")
                if stderr:
                    print(f"Stderr: {stderr[:500]}")
                print("=" * 60)

            # Parse final response and token usage
            final_response = self._parse_final_response(stdout)
            token_usage = self._parse_token_usage(stdout)
            model_used = self.model

            # Calculate cost
            cost_usd = 0.0
            if token_usage:
                cost_usd = token_usage.calculate_cost(model_used)

            if self.debug and token_usage:
                print(f"Tokens: input={token_usage.input_tokens}, output={token_usage.output_tokens}, total={token_usage.total_tokens}")
                print(f"Cost: ${cost_usd:.4f}")

            # Construct message: include stderr if command failed
            if result.returncode != 0:
                error_msg = f"Codex command failed (exit code {result.returncode})"
                if stderr and stderr.strip():
                    error_msg += f"\nSTDERR: {stderr.strip()}"
                if final_response:
                    error_msg += f"\nFinal response: {final_response}"
                message = error_msg
            else:
                message = final_response or "No response detected."

            return SolverResult(
                success=result.returncode == 0,
                message=message,
                duration_seconds=duration,
                stdout=stdout,
                stderr=stderr,
                token_usage=token_usage,
                model=model_used,
                cost_usd=cost_usd,
            )

        except subprocess.TimeoutExpired:
            duration = time.time() - start_time
            return SolverResult(
                success=False,
                message=f"Codex execution timed out after {self.timeout_seconds}s",
                duration_seconds=duration,
            )
        except FileNotFoundError:
            return SolverResult(
                success=False,
                message="Codex CLI not found. Install with: npm i -g @openai/codex",
                duration_seconds=0.0,
            )
        except Exception as e:
            duration = time.time() - start_time
            error_msg = str(e)
            is_rate_limited = self.is_rate_limit_error(error_msg)

            if self.debug:
                print(f"\nERROR INVOKING CODEX: {error_msg}")
                if is_rate_limited:
                    print("⚠️  DETECTED RATE LIMIT/QUOTA ERROR")
                print("=" * 60)

            return SolverResult(
                success=False,
                message=f"Error invoking Codex: {error_msg}",
                duration_seconds=duration,
                is_rate_limited=is_rate_limited,
            )

    def _print_trajectory(self, output: str):
        """Print key events from Codex execution trajectory."""
        for line in output.strip().split("\n"):
            if not line:
                continue
            try:
                event = json.loads(line)
                event_type = event.get("type", "")

                if event_type == "turn.started":
                    print(f"\n[Turn Started]")
                elif event_type == "item.tool_call":
                    tool_name = event.get("name", "unknown")
                    args = event.get("arguments", {})
                    print(f"\n[Tool Call] {tool_name}({json.dumps(args)[:100]})")
                elif event_type == "item.tool_result":
                    print(f"[Tool Result] received")
                elif event_type == "item.message":
                    content = event.get("content", "")
                    if content:
                        preview = content[:200] + "..." if len(content) > 200 else content
                        print(f"[Message] {preview}")
                elif event_type == "turn.completed":
                    print(f"\n[Turn Completed]")
                elif event_type == "item.file_edit":
                    file_path = event.get("path", "unknown")
                    print(f"[File Edit] {file_path}")
                elif event_type == "item.shell_command":
                    cmd = event.get("command", "")
                    print(f"[Shell] {cmd[:100]}")

            except json.JSONDecodeError:
                # Non-JSON line, possibly error message
                if line.strip() and self.debug:
                    print(f"[Raw] {line[:100]}")

    def _parse_final_response(self, output: str) -> Optional[str]:
        """Parse JSON Lines output to get final response."""
        final_response = None
        for line in output.strip().split("\n"):
            if not line:
                continue
            try:
                event = json.loads(line)
                if event.get("type") == "turn.completed":
                    final_response = event.get("finalResponse", "")
                elif event.get("type") == "item.message":
                    # Save last message as fallback
                    content = event.get("content", "")
                    if content:
                        final_response = content
            except json.JSONDecodeError:
                continue
        return final_response

    def _parse_token_usage(self, output: str) -> Optional[TokenUsage]:
        """Parse JSON Lines output to get token usage."""
        total_input = 0
        total_output = 0
        total_cached = 0

        for line in output.strip().split("\n"):
            if not line:
                continue
            try:
                event = json.loads(line)
                event_type = event.get("type", "")

                # Codex JSON output may have token_count events or usage in turn.completed
                if event_type == "token_count":
                    # Handle token_count event type
                    total_input += event.get("input_tokens", 0)
                    total_output += event.get("output_tokens", 0)
                    total_cached += event.get("cached_tokens", 0)
                elif event_type == "turn.completed":
                    # Check for usage info in turn.completed
                    usage = event.get("usage", {})
                    if usage:
                        total_input += usage.get("input_tokens", 0)
                        total_output += usage.get("output_tokens", 0)
                        total_cached += usage.get("cached_tokens", 0)
                elif event_type == "response.completed":
                    # Alternative: response.completed may have usage
                    usage = event.get("usage", {})
                    if usage:
                        total_input += usage.get("input_tokens", 0)
                        total_output += usage.get("output_tokens", 0)
                        total_cached += usage.get("cache_read_input_tokens", 0)

                # Also check payload.type for nested events
                payload = event.get("payload", {})
                if isinstance(payload, dict):
                    payload_type = payload.get("type", "")
                    if payload_type == "token_count":
                        total_input += payload.get("input_tokens", 0)
                        total_output += payload.get("output_tokens", 0)
                        total_cached += payload.get("cached_tokens", 0)

            except json.JSONDecodeError:
                continue

        if total_input > 0 or total_output > 0:
            return TokenUsage(
                input_tokens=total_input,
                output_tokens=total_output,
                total_tokens=total_input + total_output,
                cache_read_tokens=total_cached,
                cache_write_tokens=0,
            )
        return None


def main():
    """Main function for testing the solver."""
    solver = CodexSolver(debug=True)
    result = solver.solve_task()
    print("\n" + "=" * 60)
    print("RESULT:")
    print("=" * 60)
    print(f"Success: {result.success}")
    print(f"Message: {result.message[:500] if result.message else 'None'}")
    print(f"Duration: {result.duration_seconds:.2f}s")


if __name__ == "__main__":
    main()
