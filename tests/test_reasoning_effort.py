import pytest

from gamedevbench.src.benchmark_runner import GodotBenchmarkRunner


def test_final_results_record_generic_effort():
    runner = GodotBenchmarkRunner(
        use_gt=False,
        agent="codex",
        effort="xhigh",
        godot_version="4.4.1.stable.official.test",
    )

    summary = runner._create_final_results_summary(0, 0, 0, 0, 0, [])

    assert summary["configuration"]["effort"] == "xhigh"
    assert summary["configuration"]["godot_version"] == "4.4.1.stable.official.test"


def test_runner_rejects_effort_for_unsupported_solver():
    with pytest.raises(ValueError, match="does not support --effort"):
        GodotBenchmarkRunner(
            use_gt=False,
            agent="gemini-cli",
            effort="high",
        )
