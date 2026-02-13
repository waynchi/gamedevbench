#!/usr/bin/env python3
"""
Abstract base class for game development benchmark solvers.

This module defines the base class that all solver implementations must inherit from,
ensuring consistent interfaces and capabilities across all solvers.
"""

from abc import ABC, abstractmethod
from typing import Optional
from gamedevbench.src.utils.data_types import SolverResult
from gamedevbench.src.utils.prompts import load_task_config, create_task_prompt


class BaseSolver(ABC):
    """Abstract base class for all game development benchmark solvers.

    All solver implementations must inherit from this class and define:
    - SUPPORTS_MCP: bool class attribute
    - SUPPORTS_SYSTEM_PROMPT: bool class attribute
    - solve_task() method
    - is_rate_limit_error() static method
    """

    # Subclasses must define these as class attributes
    SUPPORTS_MCP: bool = False
    SUPPORTS_SYSTEM_PROMPT: bool = False

    def __init__(
        self,
        timeout_seconds: int = 600,
        debug: bool = False,
        use_mcp: bool = False,
        use_runtime_video: bool = False,
    ):
        """Initialize the base solver.

        Args:
            timeout_seconds: Maximum time to wait for completion
            debug: Enable verbose output
            use_mcp: Whether to use MCP tools (raises ValueError if not supported)
            use_runtime_video: Whether to append Godot runtime video instructions to prompts

        Raises:
            ValueError: If use_mcp=True but solver doesn't support MCP
        """
        # Validate MCP support
        if use_mcp and not self.SUPPORTS_MCP:
            raise ValueError(
                f"{self.__class__.__name__} does not support MCP "
                f"(SUPPORTS_MCP={self.SUPPORTS_MCP}). "
                f"Set use_mcp=False or use a solver that supports MCP."
            )

        self.timeout_seconds = timeout_seconds
        self.debug = debug
        self.use_mcp = use_mcp
        self.use_runtime_video = use_runtime_video

    def load_config(self) -> Optional[dict]:
        """Load task configuration from current directory.

        Returns:
            Parsed task configuration dict, or None if loading fails
        """
        return load_task_config()

    def get_task_prompt(self, config: dict) -> str:
        """Get the task prompt from config.

        Args:
            config: Task configuration dict

        Returns:
            Task prompt string (minimal, instruction only, with optional MCP guidance)
        """
        return create_task_prompt(config, self.use_runtime_video, self.use_mcp)

    @abstractmethod
    def solve_task(self) -> SolverResult:
        """Solve the task in the current directory.

        This method must be implemented by all concrete solver classes.

        Returns:
            SolverResult containing success status, message, and metadata
        """
        pass

    @staticmethod
    @abstractmethod
    def is_rate_limit_error(error_message: str) -> bool:
        """Check if the error message indicates API rate limit or quota exceeded.

        This method must be implemented by all concrete solver classes.

        Args:
            error_message: Error message to check

        Returns:
            True if the error is a rate limit error, False otherwise
        """
        pass
