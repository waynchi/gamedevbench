import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from gamedevbench.src import muse_solver
from gamedevbench.src.benchmark_runner import GodotBenchmarkRunner
from gamedevbench.src.muse_solver import MuseSolver


def write_session_log(command, kwargs, events):
    session_id = command[command.index("--session-id") + 1]
    session_dir = (
        Path(kwargs["env"]["XDG_DATA_HOME"])
        / "muse"
        / "sessions"
        / "2026"
        / "08"
        / "09"
        / session_id
    )
    session_dir.mkdir(parents=True)
    (session_dir / "session.jsonl").write_text(
        "\n".join(json.dumps(event) for event in events),
        encoding="utf-8",
    )


def test_muse_command_and_event_parsing(monkeypatch):
    monkeypatch.setenv("GAMEDEVBENCH_CONFINED", "1")
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
    assert "--sandbox-network" not in captured["command"]
    assert "--disable-web-tools" in captured["command"]
    assert "--no-foreign-personal-context" in captured["command"]
    assert "--session-id" in captured["command"]
    assert "--no-session-log" not in captured["command"]


def test_muse_parses_responses_api_usage():
    response = {
        "id": "resp_123",
        "object": "response",
        "usage": {
            "input_tokens": 120,
            "input_tokens_details": {"cached_tokens": 45},
            "output_tokens": 30,
            "output_tokens_details": {"reasoning_tokens": 12},
            "total_tokens": 150,
        },
    }

    usage = MuseSolver._parse_token_usage(json.dumps(response))

    assert usage is not None
    assert usage.input_tokens == 120
    assert usage.output_tokens == 30
    assert usage.total_tokens == 150
    assert usage.cache_read_tokens == 45
    assert usage.cache_write_tokens == 0


def test_muse_rate_limit_detection_ignores_hex_sandbox_suffix():
    assert MuseSolver.is_rate_limit_error("HTTP 429: too many requests")
    assert not MuseSolver.is_rate_limit_error(
        "workspace /tmp/gamedevbench_sandbox_d587d429 completed"
    )


def test_muse_returns_logged_usage_cache_and_billing(monkeypatch):
    solver = MuseSolver(
        timeout_seconds=30,
        model="muse-spark-1.2",
        effort="ultra",
    )
    monkeypatch.setattr(solver, "load_config", lambda: {"task": "test"})
    monkeypatch.setattr(solver, "get_task_prompt", lambda config: "test prompt")
    monkeypatch.setattr(
        MuseSolver,
        "_model_pricing",
        classmethod(
            lambda cls, model: {"input": 1.25, "output": 4.25, "cached": 0.15}
        ),
    )
    captured = {}
    logged_events = [
        {
            "id": "usage-1",
            "payload_type": "runtime.session",
            "payload": {
                "kind": "run",
                "event": {
                    "kind": "model_completed",
                    "model": "muse-spark-1.2",
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 20,
                        "cached_tokens": 40,
                        "cache_read_tokens": 40,
                        "cache_write_tokens": 3,
                        "reasoning_tokens": 10,
                    },
                },
            },
        },
        {
            "id": "usage-2",
            "payload_type": "runtime.session",
            "payload": {
                "kind": "run",
                "event": {
                    "kind": "model_completed",
                    "model": "muse-spark-1.2",
                    "usage": {
                        "input_tokens": 50,
                        "output_tokens": 10,
                        "cached_tokens": 5,
                        "cache_read_tokens": 5,
                        "cache_write_tokens": 0,
                        "reasoning_tokens": 4,
                    },
                },
            },
        },
    ]
    stdout_events = [
        {
            "payload_type": "run.terminal.completed",
            "payload": {"terminal": "completed", "text": "done"},
        }
    ]

    def fake_run(command, **kwargs):
        captured["data_home"] = Path(kwargs["env"]["XDG_DATA_HOME"])
        write_session_log(command, kwargs, logged_events)
        return SimpleNamespace(
            returncode=0,
            stdout="\n".join(json.dumps(event) for event in stdout_events),
            stderr="",
        )

    monkeypatch.setattr(muse_solver.subprocess, "run", fake_run)
    result = solver.solve_task()

    assert result.success
    assert result.token_usage is not None
    assert result.token_usage.input_tokens == 150
    assert result.token_usage.output_tokens == 30
    assert result.token_usage.total_tokens == 180
    assert result.token_usage.cache_read_tokens == 45
    assert result.token_usage.cache_write_tokens == 3
    assert result.cost_usd == pytest.approx(0.0002655)
    assert not captured["data_home"].exists()


def test_muse_preserves_completed_usage_on_timeout(monkeypatch):
    solver = MuseSolver(
        timeout_seconds=30,
        model="muse-spark-1.2",
        effort="high",
    )
    monkeypatch.setattr(solver, "load_config", lambda: {"task": "test"})
    monkeypatch.setattr(solver, "get_task_prompt", lambda config: "test prompt")
    monkeypatch.setattr(
        MuseSolver,
        "_model_pricing",
        classmethod(
            lambda cls, model: {"input": 1.25, "output": 4.25, "cached": 0.15}
        ),
    )
    logged_event = {
        "id": "usage-before-timeout",
        "payload_type": "runtime.session",
        "payload": {
            "kind": "run",
            "event": {
                "kind": "model_completed",
                "model": "muse-spark-1.2",
                "usage": {
                    "input_tokens": 75,
                    "output_tokens": 15,
                    "cached_tokens": 25,
                    "cache_read_tokens": 25,
                    "cache_write_tokens": 0,
                    "reasoning_tokens": 5,
                },
            },
        },
    }

    def fake_run(command, **kwargs):
        write_session_log(command, kwargs, [logged_event])
        raise subprocess.TimeoutExpired(
            command,
            kwargs["timeout"],
            output=b'{"payload_type":"run.output.delta"}\n',
            stderr=b"partial stderr",
        )

    monkeypatch.setattr(muse_solver.subprocess, "run", fake_run)
    result = solver.solve_task()

    assert not result.success
    assert result.message == "Muse execution timed out after 30s"
    assert result.stdout.startswith('{"payload_type"')
    assert result.stderr == "partial stderr"
    assert result.token_usage is not None
    assert result.token_usage.input_tokens == 75
    assert result.token_usage.output_tokens == 15
    assert result.token_usage.cache_read_tokens == 25
    assert result.cost_usd > 0


def test_benchmark_summary_preserves_cache_usage():
    runner = SimpleNamespace(
        agent="muse",
        model="muse-spark-1.2",
        use_mcp=False,
        use_runtime_video=True,
        skip_display=False,
        debug=False,
        run_name="usage-test",
        parallel=5,
        effort="ultra",
        godot_version="4.4.1",
    )
    results = [
        {
            "input_tokens": 100,
            "output_tokens": 20,
            "total_tokens": 120,
            "cache_read_tokens": 40,
            "cache_write_tokens": 3,
            "cost_usd": 0.001,
            "solver_duration": 10.0,
        },
        {
            "input_tokens": 50,
            "output_tokens": 10,
            "total_tokens": 60,
            "cache_read_tokens": 5,
            "cache_write_tokens": 0,
            "cost_usd": 0.0005,
            "solver_duration": 5.0,
        },
    ]

    summary = GodotBenchmarkRunner._create_final_results_summary(
        runner,
        success_count=2,
        failure_count=0,
        error_count=0,
        skipped_count=0,
        total_tasks=2,
        results=results,
    )

    token_stats = summary["token_statistics"]
    assert token_stats["total_input_tokens"] == 150
    assert token_stats["total_output_tokens"] == 30
    assert token_stats["total_cache_read_tokens"] == 45
    assert token_stats["total_cache_write_tokens"] == 3
    assert token_stats["avg_cache_read_tokens"] == 22.5
    assert token_stats["avg_cache_write_tokens"] == 1.5
    assert summary["cost_statistics"]["total_cost_usd"] == 0.0015
