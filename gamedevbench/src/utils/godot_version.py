"""Godot executable and supported-version validation."""

import re
import subprocess

from gamedevbench.src.utils.constants import (
    GODOT_EXEC_PATH,
    SUPPORTED_GODOT_VERSION,
)


class GodotVersionError(RuntimeError):
    """Raised when Godot is missing or has an unsupported version."""


def get_godot_version(executable: str = GODOT_EXEC_PATH) -> str:
    """Return the full version string reported by a Godot executable."""
    try:
        result = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except FileNotFoundError as error:
        raise GodotVersionError(
            f"Godot executable not found: {executable!r}. Install Godot "
            f"{SUPPORTED_GODOT_VERSION} or set GODOT_EXEC_PATH."
        ) from error
    except (OSError, subprocess.TimeoutExpired) as error:
        raise GodotVersionError(
            f"Could not run {executable!r} --version: {error}"
        ) from error

    output = (result.stdout or result.stderr).strip()
    if result.returncode != 0 or not output:
        raise GodotVersionError(
            f"Could not determine Godot version from {executable!r}."
        )
    return output.splitlines()[0].strip()


def require_supported_godot(executable: str = GODOT_EXEC_PATH) -> str:
    """Require the exact supported Godot semantic version."""
    version = get_godot_version(executable)
    match = re.match(r"^(\d+\.\d+\.\d+)", version)
    detected = match.group(1) if match else None
    if detected != SUPPORTED_GODOT_VERSION:
        raise GodotVersionError(
            f"GameDevBench requires Godot {SUPPORTED_GODOT_VERSION}; "
            f"{executable!r} reports {version!r}. Set GODOT_EXEC_PATH to "
            "the supported executable."
        )
    return version
