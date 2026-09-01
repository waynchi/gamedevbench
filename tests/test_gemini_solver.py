import asyncio

from gamedevbench.src.gemini_solver import GeminiSolver


def test_gemini_confined_mode_loads_admin_web_policy(monkeypatch):
    monkeypatch.setenv("GAMEDEVBENCH_CONFINED", "1")
    solver = GeminiSolver(timeout_seconds=30, model="gemini-test")
    monkeypatch.setattr(solver, "load_config", lambda: {"task": "test"})
    monkeypatch.setattr(solver, "get_task_prompt", lambda config: "test prompt")
    captured = {}

    class FakeProcess:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def fake_create_subprocess_exec(*command, **kwargs):
        captured["command"] = command
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    result = asyncio.run(solver.solve_task_async())

    assert result.success
    command = captured["command"]
    policy_index = command.index("--admin-policy")
    assert command[policy_index + 1] == (
        "/home/sandbox/.gemini/policies/gamedevbench.toml"
    )
