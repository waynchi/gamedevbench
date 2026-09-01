#!/usr/bin/env python3
"""Execute one solver inside the external confinement boundary."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import time
from pathlib import Path

from gamedevbench.src.solver_factory import SolverFactory
from gamedevbench.src.utils.data_types import SolverResult


def _write_result(path: Path, result: SolverResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(result.to_dict()), encoding="utf-8")
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Confined GameDevBench solver worker")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    started = time.time()
    try:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        secret_environment_file = config.pop("secret_environment_file", None)
        if secret_environment_file:
            secret_path = Path(secret_environment_file)
            try:
                secret_environment = json.loads(secret_path.read_text(encoding="utf-8"))
            finally:
                secret_path.unlink(missing_ok=True)
            if not isinstance(secret_environment, dict) or not all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in secret_environment.items()
            ):
                raise ValueError("Invalid confined secret environment")
            os.environ.update(secret_environment)
        with contextlib.redirect_stdout(sys.stderr):
            solver = SolverFactory.create_solver(
                agent=config["agent"],
                debug=config.get("debug", False),
                model=config.get("model"),
                use_mcp=config.get("use_mcp", False),
                timeout_seconds=config.get("timeout_seconds"),
                use_runtime_video=config.get("use_runtime_video", False),
                effort=config.get("effort"),
            )
            result = solver.solve_task()
    except Exception as error:
        result = SolverResult(
            success=False,
            message=f"Confined solver worker failed: {error}",
            duration_seconds=time.time() - started,
            stderr=str(error),
        )
    _write_result(args.output, result)


if __name__ == "__main__":
    main()
