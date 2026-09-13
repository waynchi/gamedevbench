"""Godot executable and supported-version validation."""

import re
import subprocess
import sys

from gamedevbench.src.utils.constants import (
    GODOT_ALLOW_NEWER,
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


def _parse_semantic_version(version: str):
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", version)
    return tuple(int(part) for part in match.groups()) if match else None


def require_supported_godot(
    executable: str = GODOT_EXEC_PATH, allow_newer: bool = GODOT_ALLOW_NEWER
) -> str:
    """Require the supported Godot version, or a newer one when allowed."""
    version = get_godot_version(executable)
    detected = _parse_semantic_version(version)
    supported = _parse_semantic_version(SUPPORTED_GODOT_VERSION)
    if detected == supported:
        return version

    is_newer = detected is not None and detected > supported
    if is_newer and allow_newer:
        print(
            f"Warning: running with Godot {version}; official GameDevBench "
            f"results use {SUPPORTED_GODOT_VERSION} and are not comparable.",
            file=sys.stderr,
            flush=True,
        )
        return version

    requirement = f"Godot {SUPPORTED_GODOT_VERSION}" + (" or newer" if allow_newer else "")
    hint = " Or set GODOT_ALLOW_NEWER=1 to run with a newer version anyway." if is_newer else ""
    raise GodotVersionError(
        f"GameDevBench requires {requirement}; "
        f"{executable!r} reports {version!r}. Set GODOT_EXEC_PATH to "
        f"a supported executable.{hint}"
    )
