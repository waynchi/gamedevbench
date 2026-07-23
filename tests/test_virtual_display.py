import pytest

from gamedevbench.src import virtual_display


@pytest.mark.parametrize(
    ("command", "skip_display", "environ", "platform", "expected"),
    [
        ("run", False, {}, "linux", True),
        ("validate", False, {}, "linux", True),
        ("run", True, {}, "linux", False),
        ("validate", True, {}, "linux", False),
        ("list", False, {}, "linux", False),
        ("run", False, {"DISPLAY": ":1"}, "linux", False),
        ("run", False, {}, "darwin", False),
    ],
)
def test_needs_virtual_display(
    command, skip_display, environ, platform, expected
):
    assert (
        virtual_display.needs_virtual_display(
            command,
            skip_display,
            environ=environ,
            platform=platform,
        )
        is expected
    )


def test_ensure_virtual_display_reexecs_under_xvfb(monkeypatch):
    monkeypatch.setattr(virtual_display.sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setattr(
        virtual_display.shutil,
        "which",
        lambda executable: f"/usr/bin/{executable}",
    )

    captured = {}

    def fake_execvpe(executable, args, environ):
        captured.update(
            executable=executable,
            args=args,
            environ=environ,
        )
        raise RuntimeError("exec intercepted")

    monkeypatch.setattr(virtual_display.os, "execvpe", fake_execvpe)

    with pytest.raises(RuntimeError, match="exec intercepted"):
        virtual_display.ensure_virtual_display(
            "run",
            False,
            argv=["--agent", "claude-code", "run", "task_0069"],
        )

    assert captured["executable"] == "/usr/bin/xvfb-run"
    assert captured["args"][:5] == [
        "/usr/bin/xvfb-run",
        "-a",
        virtual_display.sys.executable,
        "-m",
        "gamedevbench.src.benchmark_runner",
    ]
    assert captured["args"][5:] == [
        "--agent",
        "claude-code",
        "run",
        "task_0069",
    ]
    assert (
        captured["environ"][virtual_display.VIRTUAL_DISPLAY_ENV] == "1"
    )


def test_ensure_virtual_display_requires_os_packages(monkeypatch):
    monkeypatch.setattr(virtual_display.sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setattr(virtual_display.shutil, "which", lambda executable: None)

    with pytest.raises(
        virtual_display.VirtualDisplayError,
        match="Install the 'xvfb' and 'xauth'",
    ):
        virtual_display.ensure_virtual_display("run", False, argv=[])
