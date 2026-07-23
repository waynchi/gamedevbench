#!/usr/bin/env python3
"""
Claude Code solver for gamedev benchmark tasks.
"""

import asyncio
import json
import time
import os
from typing import Optional

from gamedevbench.src.base_solver import BaseSolver
from gamedevbench.src.utils.data_types import SolverResult, TokenUsage
from gamedevbench.src.utils.prompts import create_system_prompt

import claude_agent_sdk._internal.message_parser as mp
_original = mp.parse_message

def _tolerant_parse(data):
    try:
        return _original(data)
    except mp.MessageParseError as e:
        if "Unknown message type" in str(e):
            return None
        raise

mp.parse_message = _tolerant_parse

# Also patch the client module's imported reference
import claude_agent_sdk.client as client_mod
if hasattr(client_mod, 'parse_message'):
    client_mod.parse_message = _tolerant_parse

from claude_agent_sdk import query, ClaudeAgentOptions


class ClaudeCodeSolver(BaseSolver):
    """Solver that uses Claude Code to complete game development tasks."""

    # Solver capabilities (required by BaseSolver)
    SUPPORTS_MCP = True
    SUPPORTS_SYSTEM_PROMPT = True
    SUPPORTS_EFFORT = True

    def __init__(
        self,
        timeout_seconds: int = 600,
        debug: bool = False,
        use_mcp: bool = False,
        use_runtime_video: bool = False,
        model: Optional[str] = None,
        effort: Optional[str] = None,
    ):
        """Initialize the Claude Code solver."""
        # Call parent constructor (handles MCP validation)
        super().__init__(timeout_seconds, debug, use_mcp, use_runtime_video)
        self.model = model
        self.effort = effort

    @staticmethod
    def is_rate_limit_error(error_message: str) -> bool:
        """Check if the error message indicates API rate limit or quota exceeded."""
        error_lower = error_message.lower()
        rate_limit_keywords = [
            "overloaded",
            "rate limit",
            "rate_limit",
            "ratelimit",
            "quota exceeded",
            "quota_exceeded",
            "429",
            "too many requests",
            "capacity",
            "usage limit",
        ]
        return any(keyword in error_lower for keyword in rate_limit_keywords)

    async def solve_task_async(self) -> SolverResult:
        """Solve the task in the current directory using Claude Code SDK."""
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
            print("SENDING PROMPT TO CLAUDE CODE:")
            print("=" * 60)
            print(prompt)
            print("=" * 60)

        try:
            if self.debug:
                print("\nCLAUDE CODE TRAJECTORY:")
                print("=" * 60)

            options_kwargs = dict(
                system_prompt=create_system_prompt(self.use_mcp),
                permission_mode="bypassPermissions",
                cwd=os.getcwd(),
            )

            if self.model:
                options_kwargs["model"] = self.model

            if self.effort:
                options_kwargs["effort"] = self.effort

            if self.use_mcp:
                options_kwargs["mcp_servers"] = {
                    "godot-screenshot": {
                        "type": "stdio",
                        "command": "uv",
                        "args": ["run", "gamedevbench-mcp"],
                    }
                }
                options_kwargs["allowed_tools"] = [
                    "mcp__godot-screenshot__godot-screenshot"
                ]

            options = ClaudeAgentOptions(**options_kwargs)

            result = await asyncio.wait_for(
                self._run_claude_query(prompt, options),
                timeout=self.timeout_seconds if self.timeout_seconds else None,
            )

            full_response = result["full_response"]
            token_usage = result["token_usage"]
            total_cost = result["total_cost"]
            model_used = result["model_used"]

            duration = time.time() - start_time
            response_text = "".join(full_response)

            if self.debug:
                print(f"\n\nDuration: {duration:.2f} seconds")
                if token_usage.total_tokens > 0:
                    print(f"Tokens: input={token_usage.input_tokens}, output={token_usage.output_tokens}, total={token_usage.total_tokens}")
                    print(f"Cost: ${total_cost:.4f}")
                print("=" * 60)

            result = SolverResult(
                success=True,
                message="Task completed",
                duration_seconds=duration,
                stdout=response_text,
                stderr="",
                token_usage=token_usage if token_usage.total_tokens > 0 else None,
                model=model_used,
                cost_usd=total_cost,
            )
            return result

        except asyncio.TimeoutError:
            duration = time.time() - start_time
            if self.debug:
                print(
                    f"\nCLAUDE CODE EXECUTION TIMED OUT AFTER "
                    f"{self.timeout_seconds}s"
                )
                print("=" * 60)

            return SolverResult(
                success=False,
                message=f"Claude Code execution timed out after {self.timeout_seconds}s",
                duration_seconds=duration,
            )

        except Exception as e:
            duration = time.time() - start_time
            error_msg = str(e)
            is_rate_limited = self.is_rate_limit_error(error_msg)

            if self.debug:
                print(f"\nERROR INVOKING CLAUDE CODE: {error_msg}")
                if is_rate_limited:
                    print("⚠️  DETECTED RATE LIMIT/QUOTA ERROR")
                print("=" * 60)

            return SolverResult(
                success=False,
                message=f"Error invoking Claude Code: {error_msg}",
                duration_seconds=duration,
                is_rate_limited=is_rate_limited,
            )

    async def _run_claude_query(self, prompt: str, options: ClaudeAgentOptions) -> dict:
        full_response = []
        token_usage = TokenUsage()
        total_cost = 0.0
        model_used = self.model or "claude-sonnet-4-20250514"  # Default model

        async for message in query(prompt=prompt, options=options):
            if self.debug:
                print(message, end="", flush=True)
            full_response.append(str(message))

            # Check for ResultMessage which contains usage info
            if hasattr(message, 'usage') and message.usage:
                usage = message.usage
                # Accumulate token usage across all messages
                token_usage.input_tokens += usage.get('input_tokens', 0)
                token_usage.output_tokens += usage.get('output_tokens', 0)
                token_usage.total_tokens = (
                    token_usage.input_tokens + token_usage.output_tokens
                )
                token_usage.cache_read_tokens += usage.get(
                    'cache_read_input_tokens', 0
                )
                token_usage.cache_write_tokens += usage.get(
                    'cache_creation_input_tokens', 0
                )

            if hasattr(message, 'total_cost_usd') and message.total_cost_usd:
                # Accumulate cost across all messages
                total_cost += message.total_cost_usd

            if hasattr(message, 'model') and message.model:
                model_used = message.model

        return {
            "full_response": full_response,
            "token_usage": token_usage,
            "total_cost": total_cost,
            "model_used": model_used,
        }

    def solve_task(self) -> SolverResult:
        """Synchronous wrapper for async solve_task_async."""
        return asyncio.run(self.solve_task_async())


def main():
    """Main function for testing the solver."""
    solver = ClaudeCodeSolver()
    result = solver.solve_task()
    print(result)


if __name__ == "__main__":
    main()
