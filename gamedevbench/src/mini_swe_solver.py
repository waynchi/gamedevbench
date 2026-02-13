#!/usr/bin/env python3
"""
Mini-SWE-Agent solver for gamedev benchmark tasks.
"""

import subprocess
import json
import time
import os
from typing import Optional

from gamedevbench.src.base_solver import BaseSolver
from gamedevbench.src.utils.data_types import SolverResult


class MiniSweSolver(BaseSolver):
    """Solver that uses Mini-SWE-Agent to complete game development tasks."""

    # Solver capabilities (required by BaseSolver)
    SUPPORTS_MCP = True  # Pre-configured in CLI
    SUPPORTS_SYSTEM_PROMPT = False

    def __init__(
        self,
        timeout_seconds: int = 300,
        debug: bool = False,
        model: str = "claude",
        use_mcp: bool = False,
        use_runtime_video: bool = False,
    ):
        """
        Initialize the Mini-SWE-Agent solver.

        Args:
            timeout_seconds: Maximum time to wait for solver
            debug: Whether to show debug output
            model: Model to use ("claude" or "gpt")
            use_mcp: Whether to use MCP tools
            use_runtime_video: Whether to append Godot runtime video instructions to prompts
        """
        # Call parent constructor (handles MCP validation)
        super().__init__(timeout_seconds, debug, use_mcp, use_runtime_video)

        # Mini-SWE specific parameters
        self.model = model  # "claude" or "gpt"
        # Note: Mini-SWE assumes MCP is pre-configured in the CLI

    @staticmethod
    def is_rate_limit_error(error_message: str) -> bool:
        """Check if the error message indicates API rate limit or quota exceeded."""
        error_lower = error_message.lower()
        rate_limit_keywords = [
            "rate limit", "rate_limit", "ratelimit",
            "quota exceeded", "429", "too many requests",
        ]
        return any(keyword in error_lower for keyword in rate_limit_keywords)

    def solve_task(self) -> SolverResult:
        """Solve the task in the current directory using Mini-SWE-Agent."""
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
            print("SENDING PROMPT TO MINI-SWE-AGENT:")
            print("=" * 60)
            print(prompt)
            print("=" * 60)

        try:
            # Determine model flag
            model_flag = "-c" if self.model == "claude" else "-g"

            # Build command
            # Note: MCP server is already loaded in mini-swe-agent, no need to configure
            cmd = [
                "mini-swe-agent-mcp",
                model_flag,
                prompt
            ]

            if self.debug:
                print("\nMINI-SWE-AGENT COMMAND:")
                print("=" * 60)
                print(" ".join(cmd))
                print("=" * 60)
                print("\nMINI-SWE-AGENT TRAJECTORY:")
                print("=" * 60)

            # Execute Mini-SWE-Agent
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                cwd=os.getcwd()
            )

            duration = time.time() - start_time

            stdout = result.stdout
            stderr = result.stderr

            if self.debug:
                if stdout:
                    print(stdout)
                if stderr:
                    print("STDERR:", stderr)
                print(f"\n\nDuration: {duration:.2f} seconds")
                print(f"Return code: {result.returncode}")
                print("=" * 60)

            # Consider success if return code is 0
            success = result.returncode == 0

            return SolverResult(
                success=success,
                message="Task completed" if success else f"Mini-SWE-Agent returned code {result.returncode}",
                duration_seconds=duration,
                stdout=stdout,
                stderr=stderr,
            )

        except subprocess.TimeoutExpired:
            duration = time.time() - start_time
            if self.debug:
                print(f"\nMINI-SWE-AGENT TIMEOUT after {duration:.2f} seconds")
                print("=" * 60)

            return SolverResult(
                success=False,
                message=f"Mini-SWE-Agent timed out after {self.timeout_seconds} seconds",
                duration_seconds=duration,
            )

        except FileNotFoundError:
            duration = time.time() - start_time
            if self.debug:
                print("\nERROR: mini-swe-agent-mcp command not found")
                print("=" * 60)

            return SolverResult(
                success=False,
                message="mini-swe-agent-mcp command not found. Make sure it's installed and in PATH.",
                duration_seconds=duration,
            )

        except Exception as e:
            duration = time.time() - start_time
            if self.debug:
                print(f"\nERROR INVOKING MINI-SWE-AGENT: {str(e)}")
                print("=" * 60)

            return SolverResult(
                success=False,
                message=f"Error invoking Mini-SWE-Agent: {str(e)}",
                duration_seconds=duration,
            )


def main():
    """Main function for testing the solver."""
    solver = MiniSweSolver(debug=True)
    result = solver.solve_task()
    print(result)


if __name__ == "__main__":
    main()
