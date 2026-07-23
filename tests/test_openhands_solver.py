import pytest

pytest.importorskip("openhands.sdk")

from gamedevbench.src import openhands_solver  # noqa: E402
from gamedevbench.src.openhands_solver import OpenHandsSolver  # noqa: E402


def test_openhands_effort_maps_to_llm_reasoning_effort(monkeypatch):
    captured = {}

    def fake_llm(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(openhands_solver, "LLM", fake_llm)
    OpenHandsSolver(model="openai/gpt-5.6", effort="high")._create_llm(
        "test-key"
    )

    assert captured["reasoning_effort"] == "high"


def test_openhands_unset_effort_preserves_default(monkeypatch):
    captured = {}

    def fake_llm(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(openhands_solver, "LLM", fake_llm)
    OpenHandsSolver(model="openai/gpt-5.6")._create_llm("test-key")

    assert "reasoning_effort" not in captured
