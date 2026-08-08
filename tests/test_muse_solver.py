import json
from types import SimpleNamespace

from gamedevbench.src import muse_solver
from gamedevbench.src.muse_solver import MuseSolver


def test_muse_command_and_event_parsing(monkeypatch):
    solver = MuseSolver(
        timeout_seconds=30,
        model="muse-spark-1.2-contributor",
        effort="high",
        use_runtime_video=True,
    )
    monkeypatch.setattr(solver, "load_config", lambda: {"task": "test"})
    monkeypatch.setattr(solver, "get_task_prompt", lambda config: "test prompt")
    captured = {}
    events = [
        {
            "payload_type": "run.model.configured",
            "payload": {"model_id": "muse-spark-1.2-contributor"},
        },
        {
            "payload_type": "run.output.delta",
            "payload": {"text": "done"},
        },
        {
            "payload_type": "run.terminal.completed",
            "payload": {"terminal": "completed", "text": "done"},
        },
    ]

    def fake_run(command, **kwargs):
        captured["command"] = command
        return SimpleNamespace(
            returncode=0,
            stdout="\n".join(json.dumps(event) for event in events),
            stderr="",
        )

    monkeypatch.setattr(muse_solver.subprocess, "run", fake_run)
    result = solver.solve_task()

    assert result.success
    assert result.message == "done"
    assert result.model == "muse-spark-1.2-contributor"
    assert captured["command"][:3] == ["muse", "exec", "--json"]
    assert captured["command"][-1] == "test prompt"
    assert captured["command"][captured["command"].index("--model") + 1] == "muse-spark-1.2-contributor"
    assert captured["command"][captured["command"].index("--reasoning-effort") + 1] == "high"
    assert "--disable-approval" in captured["command"]
    assert "--disable-sandbox" in captured["command"]
