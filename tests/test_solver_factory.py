import pytest

from gamedevbench.src.solver_factory import SolverFactory


def test_factory_passes_effort_to_supported_solvers():
    claude = SolverFactory.create_solver("claude-code", effort="high")
    codex = SolverFactory.create_solver("codex", effort="xhigh")
    opencode = SolverFactory.create_solver("opencode", effort="max")

    assert claude.effort == "high"
    assert codex.effort == "xhigh"
    assert opencode.effort == "max"

    expected = ["claude-code", "codex", "opencode"]
    if "openhands" in SolverFactory.get_available_agents():
        openhands = SolverFactory.create_solver("openhands", effort="medium")
        assert openhands.effort == "medium"
        expected.append("openhands")

    assert SolverFactory.get_effort_capable_solvers() == expected
    for agent in expected:
        assert SolverFactory.get_solver_info(agent)["supports_effort"]


def test_factory_rejects_effort_for_unsupported_solver():
    with pytest.raises(ValueError, match="does not support --effort"):
        SolverFactory.create_solver("gemini-cli", effort="high")
