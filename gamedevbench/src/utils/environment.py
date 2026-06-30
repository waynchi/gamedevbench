#!/usr/bin/env python3
"""Capture reproducibility metadata for benchmark runs."""

import platform
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from gamedevbench.src.utils.constants import GODOT_EXEC_PATH, PROJECT_ROOT


AGENT_VERSION_COMMANDS = {
    "claude-code": ["claude", "--version"],
    "opencode": ["opencode", "--version"],
    "omo": ["opencode", "--version"],
    "pi": ["pi", "--version"],
    "pi-stock": ["pi", "--version"],
    "codex": ["codex", "--version"],
    "gemini-cli": ["gemini", "--version"],
    "mini-swe": ["mini-swe-agent-mcp", "--version"],
}


def collect_environment_metadata(
    agent: Optional[str],
    capture_source: str = "runtime",
) -> dict:
    """Collect tool and repository versions once at benchmark startup."""
    agent_command = AGENT_VERSION_COMMANDS.get(agent)
    metadata = {
        "captured_at": datetime.now().isoformat(),
        "capture_source": capture_source,
        "agent_cli": {
            "name": agent,
            "version": _command_version(agent_command),
        },
        "godot": {
            "executable": GODOT_EXEC_PATH,
            "version": _command_version([GODOT_EXEC_PATH, "--version"]),
        },
        "python": {
            "version": platform.python_version(),
            "executable": sys.executable,
        },
        "platform": platform.platform(),
        "gamedevbench": _git_metadata(PROJECT_ROOT),
    }
    variant = _variant_metadata(agent)
    if variant:
        metadata["variant"] = variant
    return metadata


def _variant_metadata(agent: Optional[str]) -> Optional[dict]:
    if agent == "pi-stock":
        config_dir = Path(
            os.environ.get(
                "GAMEDEVBENCH_PI_STOCK_CONFIG_DIR",
                PROJECT_ROOT / ".benchmark-config" / "pi-stock",
            )
        )
        return {
            "name": "pi-stock",
            "config_dir": str(config_dir),
            "system_prompt": "built-in",
            "system_md_present": any(
                (config_dir / name).exists() for name in ("SYSTEM.md", "system.md")
            ),
            "settings_sha256": _file_sha256(config_dir / "settings.json"),
            "models_sha256": _file_sha256(config_dir / "models.json"),
        }
    if agent == "omo":
        xdg_home = Path(
            os.environ.get(
                "GAMEDEVBENCH_OMO_XDG_CONFIG_HOME",
                PROJECT_ROOT / ".benchmark-config" / "omo",
            )
        )
        config_dir = xdg_home / "opencode"
        package_json = config_dir / "node_modules" / "oh-my-openagent" / "package.json"
        return {
            "name": "omo",
            "primary_agent": "Sisyphus - ultraworker",
            "xdg_config_home": str(xdg_home),
            "opencode_config_sha256": _file_sha256(config_dir / "opencode.json"),
            "omo_config_sha256": _first_file_sha256(
                config_dir,
                ("oh-my-openagent.jsonc", "oh-my-opencode.jsonc"),
            ),
            "omo_version": _package_version(package_json)
            or _text_value(config_dir / "benchmark-omo-version.txt"),
        }
    return None


def _file_sha256(path: Path) -> Optional[str]:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _first_file_sha256(directory: Path, names: tuple[str, ...]) -> Optional[str]:
    for name in names:
        value = _file_sha256(directory / name)
        if value:
            return value
    return None


def _package_version(path: Path) -> Optional[str]:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("version")
    except (OSError, json.JSONDecodeError):
        return None


def _text_value(path: Path) -> Optional[str]:
    if not path.is_file():
        return None
    value = path.read_text(encoding="utf-8-sig").strip()
    return value or None


def _command_version(command: Optional[list[str]]) -> Optional[str]:
    if not command:
        return None

    executable = command[0]
    resolved = shutil.which(executable) if not Path(executable).is_file() else executable
    if not resolved:
        return None

    try:
        result = subprocess.run(
            [resolved, *command[1:]],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None

    output = (result.stdout or result.stderr).strip()
    return output.splitlines()[0] if output else None


def _git_metadata(repo: Path) -> dict:
    return {
        "commit": _git_value(repo, "rev-parse", "HEAD"),
        "branch": _git_value(repo, "branch", "--show-current"),
        "dirty": bool(_git_value(repo, "status", "--porcelain")),
    }


def _git_value(repo: Path, *args: str) -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repo,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None

    value = result.stdout.strip()
    return value or None
