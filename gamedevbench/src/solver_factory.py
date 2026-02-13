#!/usr/bin/env python3
"""
Factory for creating solver instances based on agent type.
Uses registry pattern for modular solver management.
"""
from typing import Dict, Type, Optional
from gamedevbench.src.base_solver import BaseSolver
from gamedevbench.src.claude_code_solver import ClaudeCodeSolver
from gamedevbench.src.mini_swe_solver import MiniSweSolver
from gamedevbench.src.codex_solver import CodexSolver
from gamedevbench.src.gemini_solver import GeminiSolver

# OpenHands requires Python 3.12+, make it optional
try:
    from gamedevbench.src.openhands_solver import OpenHandsSolver
    OPENHANDS_AVAILABLE = True
except ImportError:
    OpenHandsSolver = None
    OPENHANDS_AVAILABLE = False


class SolverFactory:
    """Factory for creating solver instances with proper configuration."""

    # Registry mapping agent names to solver classes
    _SOLVER_REGISTRY: Dict[str, Type[BaseSolver]] = {
        "claude-code": ClaudeCodeSolver,
        "mini-swe": MiniSweSolver,
        "codex": CodexSolver,
        "gemini-cli": GeminiSolver,
    }

    # Conditionally add OpenHands if available
    if OPENHANDS_AVAILABLE:
        _SOLVER_REGISTRY["openhands"] = OpenHandsSolver

    @classmethod
    def create_solver(
        cls,
        agent: str,
        debug: bool = False,
        model: Optional[str] = None,
        use_mcp: bool = False,
        timeout_seconds: int = 600,
        use_runtime_video: bool = False,
    ) -> BaseSolver:
        """
        Create a solver instance based on agent type.

        Args:
            agent: Agent name (e.g., "claude-code", "mini-swe", "openhands", "codex", "gemini-cli")
            debug: Enable debug output
            model: Model name (used by solvers that support model selection)
            use_mcp: Enable MCP server functionality (will validate solver supports it)
            timeout_seconds: Maximum time for solver execution
            use_runtime_video: Enable runtime video mode (appends Godot runtime instructions to prompts)

        Returns:
            Configured solver instance

        Raises:
            ValueError: If agent is unknown or if MCP is requested but not supported
            RuntimeError: If OpenHands is requested but Python version < 3.12
        """
        # Check if agent exists in registry
        if agent not in cls._SOLVER_REGISTRY:
            available_agents = ", ".join(cls._SOLVER_REGISTRY.keys())
            raise ValueError(
                f"Unknown agent: {agent}. Available agents: {available_agents}"
            )

        # Special case: OpenHands availability check
        if agent == "openhands" and not OPENHANDS_AVAILABLE:
            raise RuntimeError(
                "OpenHands requires Python 3.12+. Please upgrade your Python version."
            )

        solver_class = cls._SOLVER_REGISTRY[agent]

        # Validate MCP support before instantiation (additional safety check)
        if use_mcp and not solver_class.SUPPORTS_MCP:
            raise ValueError(
                f"Agent '{agent}' does not support MCP. "
                f"Set use_mcp=False or use a solver that supports MCP. "
                f"Solvers with MCP support: {cls.get_mcp_capable_solvers()}"
            )

        # Build kwargs based on what each solver accepts
        kwargs = {
            "debug": debug,
            "timeout_seconds": timeout_seconds,
            "use_runtime_video": use_runtime_video,
        }

        # Add model parameter for solvers that support it
        if agent in ["claude-code", "mini-swe", "openhands", "gemini-cli", "codex"]:
            if model:
                kwargs["model"] = model

        # Add use_mcp for solvers that support it
        if solver_class.SUPPORTS_MCP:
            kwargs["use_mcp"] = use_mcp

        # Create and return solver instance
        # The BaseSolver.__init__ will perform final validation
        return solver_class(**kwargs)

    @classmethod
    def get_available_agents(cls) -> list[str]:
        """Get list of available agent names."""
        return sorted(cls._SOLVER_REGISTRY.keys())

    @classmethod
    def get_mcp_capable_solvers(cls) -> list[str]:
        """Get list of solvers that support MCP."""
        return sorted(
            agent
            for agent, solver_class in cls._SOLVER_REGISTRY.items()
            if solver_class.SUPPORTS_MCP
        )

    @classmethod
    def get_solver_info(cls, agent: str) -> Dict[str, bool]:
        """
        Get capability information for a specific solver.

        Args:
            agent: Agent name

        Returns:
            Dictionary with solver capabilities (SUPPORTS_MCP, SUPPORTS_SYSTEM_PROMPT)

        Raises:
            ValueError: If agent is unknown
        """
        if agent not in cls._SOLVER_REGISTRY:
            raise ValueError(f"Unknown agent: {agent}")

        solver_class = cls._SOLVER_REGISTRY[agent]
        return {
            "supports_mcp": solver_class.SUPPORTS_MCP,
            "supports_system_prompt": solver_class.SUPPORTS_SYSTEM_PROMPT,
        }

    @classmethod
    def register_solver(cls, agent_name: str, solver_class: Type[BaseSolver]):
        """
        Register a new solver class (useful for plugins/extensions).

        Args:
            agent_name: Name to register solver under
            solver_class: Solver class (must inherit from BaseSolver)

        Raises:
            TypeError: If solver_class doesn't inherit from BaseSolver
        """
        if not issubclass(solver_class, BaseSolver):
            raise TypeError(
                f"Solver class must inherit from BaseSolver, got {solver_class}"
            )
        cls._SOLVER_REGISTRY[agent_name] = solver_class
