#!/usr/bin/env python3
"""Pi solver using Pi's built-in system prompt in an isolated config directory."""

import os
from pathlib import Path
from typing import Optional

from gamedevbench.src.pi_solver import PiSolver
from gamedevbench.src.utils.constants import PROJECT_ROOT
from gamedevbench.src.utils.data_types import SolverResult


DEFAULT_CONFIG_DIR = PROJECT_ROOT / ".benchmark-config" / "pi-stock"


class PiStockSolver(PiSolver):
    """Run Pi without a user SYSTEM.md while retaining its normal built-in tools."""

    def solve_task(self) -> SolverResult:
        config_dir = self._config_dir()
        if not (config_dir / "settings.json").is_file():
            return SolverResult(
                False,
                f"Pi stock config is not prepared: {config_dir}",
                0.0,
            )
        if any((config_dir / name).exists() for name in ("SYSTEM.md", "system.md")):
            return SolverResult(
                False,
                f"Pi stock config must not contain SYSTEM.md: {config_dir}",
                0.0,
            )
        return super().solve_task()

    def _config_dir(self) -> Path:
        override = os.environ.get("GAMEDEVBENCH_PI_STOCK_CONFIG_DIR")
        return Path(override) if override else DEFAULT_CONFIG_DIR

    def _command_environment(self) -> Optional[dict[str, str]]:
        env = os.environ.copy()
        env["PI_CODING_AGENT_DIR"] = str(self._config_dir())
        return env
