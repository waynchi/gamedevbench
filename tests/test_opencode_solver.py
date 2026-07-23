from types import SimpleNamespace

from gamedevbench.src import opencode_solver
from gamedevbench.src.opencode_solver import OpenCodeSolver


def _capture_command(monkeypatch, solver):
    monkeypatch.setattr(solver, "load_config", lambda: {"task": "test"})
    monkeypatch.setattr(solver, "get_task_prompt", lambda config: "test prompt")
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(opencode_solver.subprocess, "run", fake_run)
    assert solver.solve_task().success
    return captured["command"]


def test_opencode_effort_maps_to_model_variant(monkeypatch):
    command = _capture_command(
        monkeypatch,
        OpenCodeSolver(model="openai/gpt-5.6", effort="high"),
    )

    variant_index = command.index("--variant")
    assert command[variant_index + 1] == "high"


def test_opencode_unset_effort_preserves_default(monkeypatch):
    command = _capture_command(
        monkeypatch,
        OpenCodeSolver(model="openai/gpt-5.6"),
    )

    assert "--variant" not in command
