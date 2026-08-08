import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from gamedevbench.src.utils import godot_version


def test_godot_exec_path_environment_is_honored():
    environment = {**os.environ, "GODOT_EXEC_PATH": "/opt/godot-4.4.1"}
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from gamedevbench.src.utils.constants import GODOT_EXEC_PATH; "
            "print(GODOT_EXEC_PATH)",
        ],
        capture_output=True,
        text=True,
        check=True,
        env=environment,
    )

    assert result.stdout.strip() == "/opt/godot-4.4.1"


@pytest.mark.parametrize(
    "reported_version",
    [
        "4.4.1.stable.official.49a5bc7b6",
        "4.4.1-stable",
    ],
)
def test_supported_godot_version_is_accepted(monkeypatch, reported_version):
    monkeypatch.setattr(
        godot_version.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=reported_version + "\n",
            stderr="",
        ),
    )

    assert godot_version.require_supported_godot("godot-4.4.1") == reported_version


def test_unsupported_godot_version_is_rejected(monkeypatch):
    monkeypatch.setattr(
        godot_version.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="4.7.1.stable.official.a13da4feb\n",
            stderr="",
        ),
    )

    with pytest.raises(godot_version.GodotVersionError, match="requires Godot 4.4.1"):
        godot_version.require_supported_godot("godot-4.7.1")


def test_missing_godot_executable_has_actionable_error(monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(godot_version.subprocess, "run", missing)

    with pytest.raises(godot_version.GodotVersionError, match="GODOT_EXEC_PATH"):
        godot_version.require_supported_godot("missing-godot")
