import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from gamedevbench.src.utils import godot_version


def _constants_in_environment(expression, **variables):
    environment = {**os.environ, **variables}
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            f"from gamedevbench.src.utils.constants import *; print({expression})",
        ],
        capture_output=True,
        text=True,
        check=True,
        env=environment,
    )
    return result.stdout.strip()


def test_godot_exec_path_environment_is_honored():
    exec_path = _constants_in_environment(
        "GODOT_EXEC_PATH", GODOT_EXEC_PATH="/opt/godot-4.4.1"
    )

    assert exec_path == "/opt/godot-4.4.1"


@pytest.mark.parametrize(
    "value, expected",
    [("1", "True"), ("true", "True"), ("yes", "True"), ("0", "False"), ("", "False")],
)
def test_godot_allow_newer_environment_is_parsed(value, expected):
    assert _constants_in_environment("GODOT_ALLOW_NEWER", GODOT_ALLOW_NEWER=value) == expected


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


def _report_version(monkeypatch, reported_version):
    monkeypatch.setattr(
        godot_version.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=reported_version + "\n",
            stderr="",
        ),
    )


def test_newer_godot_version_is_rejected_by_default(monkeypatch):
    _report_version(monkeypatch, "4.7.1.stable.official.a13da4feb")

    with pytest.raises(
        godot_version.GodotVersionError,
        match=r"requires Godot 4\.4\.1;.*GODOT_ALLOW_NEWER=1",
    ):
        godot_version.require_supported_godot("godot-4.7.1", allow_newer=False)


def test_newer_godot_version_is_accepted_when_allowed(monkeypatch, capsys):
    reported_version = "4.7.1.stable.official.a13da4feb"
    _report_version(monkeypatch, reported_version)

    result = godot_version.require_supported_godot("godot-4.7.1", allow_newer=True)

    assert result == reported_version
    assert "not comparable" in capsys.readouterr().err


def test_supported_godot_version_does_not_warn_when_newer_allowed(monkeypatch, capsys):
    _report_version(monkeypatch, "4.4.1.stable.official.49a5bc7b6")

    godot_version.require_supported_godot("godot-4.4.1", allow_newer=True)

    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("reported_version", ["4.4.0.stable.official", "4.3.2.stable.official"])
def test_older_godot_version_is_rejected_even_when_newer_allowed(monkeypatch, reported_version):
    _report_version(monkeypatch, reported_version)

    with pytest.raises(godot_version.GodotVersionError, match="requires Godot 4.4.1 or newer"):
        godot_version.require_supported_godot("godot-old", allow_newer=True)


def test_unparseable_godot_version_is_rejected(monkeypatch):
    _report_version(monkeypatch, "custom-build")

    with pytest.raises(godot_version.GodotVersionError, match="requires Godot 4.4.1"):
        godot_version.require_supported_godot("godot-custom", allow_newer=True)


def test_missing_godot_executable_has_actionable_error(monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(godot_version.subprocess, "run", missing)

    with pytest.raises(godot_version.GodotVersionError, match="GODOT_EXEC_PATH"):
        godot_version.require_supported_godot("missing-godot")
