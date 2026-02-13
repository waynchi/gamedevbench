#!/usr/bin/env python3
"""
OpenHands solver for gamedev benchmark tasks.
Uses OpenHands SDK with MCP server for Godot screenshots.
"""

import json
import time
import os
from typing import Optional

from pydantic import SecretStr
from openhands.sdk import (
    LLM,
    Conversation,
    Event,
    get_logger,
    Agent,
)
from openhands.sdk.security.confirmation_policy import NeverConfirm
from openhands.tools.preset.default import get_default_tools, get_default_condenser
from gamedevbench.src.base_solver import BaseSolver
from gamedevbench.src.utils.data_types import SolverResult, TokenUsage
from gamedevbench.src.utils.prompts import create_system_prompt


logger = get_logger(__name__)


class OpenHandsSolver(BaseSolver):
    """Solver that uses OpenHands to complete game development tasks."""

    # Solver capabilities (required by BaseSolver)
    SUPPORTS_MCP = True
    SUPPORTS_SYSTEM_PROMPT = True  # Via custom_instructions

    # Model name mapping for litellm format
    MODEL_MAPPING = {
        "claude": "anthropic/claude-sonnet-4-20250514",
        "gpt": "openai/gpt-4o",
        "gpt-4o": "openai/gpt-4o",
        "gpt-4": "openai/gpt-4",
        "o1": "openai/o1",
        "o3": "openai/o3",
    }

    def __init__(
        self,
        timeout_seconds: int = 600,
        debug: bool = False,
        use_mcp: bool = False,
        model: str = "openai/gpt-4o",  # litellm format: provider/model
        use_runtime_video: bool = False,
        api_base: Optional[str] = None,
        openrouter_site_url: Optional[str] = None,
        openrouter_app_name: Optional[str] = None,
    ):
        """
        Initialize the OpenHands solver.

        Args:
            timeout_seconds: Maximum time to wait for solver
            debug: Whether to show debug output
            use_mcp: Whether to use MCP tools
            model: Model to use (default: openai/gpt-4o, supports vision)
            use_runtime_video: Whether to append Godot runtime video instructions to prompts
        """
        # Call parent constructor (handles MCP validation)
        super().__init__(timeout_seconds, debug, use_mcp, use_runtime_video)

        # OpenHands-specific parameters
        # Convert short model names to litellm format
        self.model = self.MODEL_MAPPING.get(model, model)

        # Optional overrides
        self.api_base = api_base or os.environ.get("OPENROUTER_API_BASE")
        self.openrouter_site_url = openrouter_site_url or os.environ.get("OR_SITE_URL")
        self.openrouter_app_name = openrouter_app_name or os.environ.get("OR_APP_NAME")

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
        """Solve the task in the current directory using OpenHands."""
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
            print("SENDING PROMPT TO OPENHANDS:")
            print("=" * 60)
            print(prompt)
            print("=" * 60)

        try:
            # Get API key from environment based on model provider
            if self.model.startswith("openrouter/"):
                api_key = os.environ.get("OPENROUTER_API_KEY")
                key_name = "OPENROUTER_API_KEY"
            elif self.model.startswith("fireworks_ai/") or self.model.startswith("fireworks/"):
                api_key = os.environ.get("FIREWORKS_API_KEY")
                key_name = "FIREWORKS_API_KEY"
            elif self.model.startswith("anthropic/"):
                api_key = os.environ.get("ANTHROPIC_API_KEY")
                key_name = "ANTHROPIC_API_KEY"
            elif self.model.startswith("google/") or self.model.startswith("gemini/"):
                api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
                key_name = "GEMINI_API_KEY or GOOGLE_API_KEY"
            else:
                api_key = os.environ.get("OPENAI_API_KEY")
                key_name = "OPENAI_API_KEY"

            if not api_key:
                return SolverResult(
                    success=False,
                    message=f"{key_name} environment variable not set",
                    duration_seconds=0.0,
                )

            if self.debug:
                print(f"\nUsing model: {self.model}")
                print("\nOPENHANDS TRAJECTORY:")
                print("=" * 60)

            # Configure LLM with vision-capable model
            # Explicitly cap max_output_tokens to avoid models like kimi-k2.5 where
            # litellm reports max_output_tokens == context_window (262144), making
            # any request with input tokens exceed the total context limit.
            llm_kwargs = dict(
                model=self.model,
                api_key=SecretStr(api_key),
                temperature=0.0,
                base_url=self.api_base,
                max_output_tokens=32768,
            )
            if self.model.startswith("openrouter/"):
                llm_kwargs["openrouter_site_url"] = self.openrouter_site_url or "https://docs.all-hands.dev/"
                llm_kwargs["openrouter_app_name"] = self.openrouter_app_name or "OpenHands"
            llm = LLM(**llm_kwargs)

            mcp_config = {
                "mcpServers": {
                    "godot-screenshot": {
                        "command": "uv",
                        "args": ["run", "gamedevbench-mcp"]
                    }
                }
            }

            # Create agent with default tool selection (CLI mode disables browser)
            # We construct the Agent manually because it's a frozen Pydantic model
            # and we need to inject mcp_config during initialization.
            tools = get_default_tools(
                enable_browser=False,  # CLI mode disables browser
            )
            
            if self.use_mcp:
                agent = Agent(
                    llm=llm,
                    tools=tools,
                    system_prompt_kwargs={"cli_mode": True},
                    condenser=get_default_condenser(
                        llm=llm.model_copy(update={"usage_id": "condenser"})
                    ),
                    mcp_config=mcp_config
                )
            else:
                agent = Agent(
                    llm=llm,
                    tools=tools,
                    system_prompt_kwargs={"cli_mode": True},
                    condenser=get_default_condenser(
                        llm=llm.model_copy(update={"usage_id": "condenser"})
                    ),
                )

            # Collect output for logging and token tracking
            output_lines = []
            token_usage = TokenUsage()

            def event_callback(event: Event):
                """Callback to handle and log events."""
                nonlocal token_usage
                event_str = str(event)
                output_lines.append(event_str)

                # Try to extract token usage from events
                if hasattr(event, 'usage'):
                    usage = event.usage
                    if isinstance(usage, dict):
                        token_usage.input_tokens += usage.get('input_tokens', 0) or usage.get('prompt_tokens', 0)
                        token_usage.output_tokens += usage.get('output_tokens', 0) or usage.get('completion_tokens', 0)
                        token_usage.total_tokens = token_usage.input_tokens + token_usage.output_tokens
                        token_usage.cache_read_tokens += usage.get('cache_read_input_tokens', 0) or usage.get('cached_tokens', 0)

                # Also check for metrics attribute
                if hasattr(event, 'metrics') and event.metrics:
                    metrics = event.metrics
                    if isinstance(metrics, dict):
                        token_usage.input_tokens += metrics.get('input_tokens', 0)
                        token_usage.output_tokens += metrics.get('output_tokens', 0)
                        token_usage.total_tokens = token_usage.input_tokens + token_usage.output_tokens

                if self.debug:
                    # Print a summary of the event
                    event_type = type(event).__name__
                    preview = event_str[:150].replace('\n', ' ')
                    print(f"\n[{event_type}] {preview}...")

            # Create conversation with workspace set to current directory
            conversation = Conversation(
                agent=agent,
                callbacks=[event_callback],
                workspace=os.getcwd(),
            )
            # Run without confirmation prompts
            conversation.set_confirmation_policy(NeverConfirm())

            # Send message and run
            conversation.send_message(prompt)
            conversation.run()

            duration = time.time() - start_time
            response_text = "\n".join(output_lines)

            # Get token usage from conversation_stats (the correct way)
            model_used = self.model
            cost_usd = 0.0

            # Access conversation stats for token/cost information
            if hasattr(conversation, 'conversation_stats') and conversation.conversation_stats:
                stats = conversation.conversation_stats
                combined_metrics = stats.get_combined_metrics()
                if combined_metrics:
                    # Get accumulated token usage
                    if combined_metrics.accumulated_token_usage:
                        usage = combined_metrics.accumulated_token_usage
                        token_usage.input_tokens = usage.prompt_tokens or 0
                        token_usage.output_tokens = usage.completion_tokens or 0
                        token_usage.cache_read_tokens = usage.cache_read_tokens or 0
                        token_usage.cache_write_tokens = usage.cache_write_tokens or 0
                        token_usage.total_tokens = token_usage.input_tokens + token_usage.output_tokens
                    # Get accumulated cost directly from metrics
                    cost_usd = combined_metrics.accumulated_cost or 0.0

            # Fallback: calculate cost if we have tokens but no cost
            if token_usage.total_tokens > 0 and cost_usd == 0.0:
                cost_usd = token_usage.calculate_cost(model_used)

            if self.debug:
                print(f"\n\nDuration: {duration:.2f} seconds")
                if token_usage.total_tokens > 0:
                    print(f"Tokens: input={token_usage.input_tokens}, output={token_usage.output_tokens}, total={token_usage.total_tokens}")
                    print(f"Cost: ${cost_usd:.4f}")
                print("=" * 60)

            return SolverResult(
                success=True,
                message="Task completed",
                duration_seconds=duration,
                stdout=response_text,
                stderr="",
                token_usage=token_usage if token_usage.total_tokens > 0 else None,
                model=model_used,
                cost_usd=cost_usd,
            )

        except Exception as e:
            duration = time.time() - start_time
            error_msg = str(e)
            is_rate_limited = self.is_rate_limit_error(error_msg)

            if self.debug:
                print(f"\nERROR INVOKING OPENHANDS: {error_msg}")
                if is_rate_limited:
                    print("⚠️  DETECTED RATE LIMIT/QUOTA ERROR")
                print("=" * 60)
                import traceback
                traceback.print_exc()

            return SolverResult(
                success=False,
                message=f"Error invoking OpenHands: {error_msg}",
                duration_seconds=duration,
                is_rate_limited=is_rate_limited,
            )


def main():
    """Main function for testing the solver."""
    solver = OpenHandsSolver(debug=True)
    result = solver.solve_task()
    print("\n" + "=" * 60)
    print("RESULT:")
    print("=" * 60)
    print(f"Success: {result.success}")
    print(f"Message: {result.message[:500] if result.message else 'None'}")
    print(f"Duration: {result.duration_seconds:.2f}s")


if __name__ == "__main__":
    main()
