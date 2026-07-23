from types import SimpleNamespace

from gamedevbench.src import codex_solver
from gamedevbench.src.codex_solver import CodexSolver


def _capture_command(monkeypatch, solver):
    monkeypatch.setattr(solver, "load_config", lambda: {"task": "test"})
    monkeypatch.setattr(solver, "get_task_prompt", lambda config: "test prompt")
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(codex_solver.subprocess, "run", fake_run)
    assert solver.solve_task().success
    return captured["command"]


def test_codex_effort_uses_per_invocation_config_override(monkeypatch):
    command = _capture_command(
        monkeypatch,
        CodexSolver(timeout_seconds=30, model="gpt-5.6", effort="xhigh"),
    )

    effort_index = command.index("-c")
    assert command[effort_index + 1] == 'model_reasoning_effort="xhigh"'


def test_codex_unset_effort_preserves_default(monkeypatch):
    command = _capture_command(
        monkeypatch,
        CodexSolver(timeout_seconds=30, model="gpt-5.6"),
    )

    assert "-c" not in command
