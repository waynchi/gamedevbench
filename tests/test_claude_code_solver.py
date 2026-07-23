import asyncio

from gamedevbench.src.claude_code_solver import ClaudeCodeSolver
from gamedevbench.src.utils.data_types import TokenUsage


def test_claude_effort_maps_to_agent_options(monkeypatch):
    solver = ClaudeCodeSolver(
        timeout_seconds=30,
        model="claude-opus-4-6",
        effort="xhigh",
    )
    monkeypatch.setattr(solver, "load_config", lambda: {"task": "test"})
    monkeypatch.setattr(solver, "get_task_prompt", lambda config: "test prompt")

    captured = {}

    async def fake_run_query(prompt, options):
        captured["options"] = options
        return {
            "full_response": [],
            "token_usage": TokenUsage(),
            "total_cost": 0.0,
            "model_used": "claude-opus-4-6",
        }

    monkeypatch.setattr(solver, "_run_claude_query", fake_run_query)

    result = asyncio.run(solver.solve_task_async())

    assert result.success
    assert captured["options"].effort == "xhigh"


def test_claude_unset_effort_preserves_default(monkeypatch):
    solver = ClaudeCodeSolver(timeout_seconds=30)
    monkeypatch.setattr(solver, "load_config", lambda: {"task": "test"})
    monkeypatch.setattr(solver, "get_task_prompt", lambda config: "test prompt")

    captured = {}

    async def fake_run_query(prompt, options):
        captured["options"] = options
        return {
            "full_response": [],
            "token_usage": TokenUsage(),
            "total_cost": 0.0,
            "model_used": "claude",
        }

    monkeypatch.setattr(solver, "_run_claude_query", fake_run_query)

    result = asyncio.run(solver.solve_task_async())

    assert result.success
    assert captured["options"].effort is None
