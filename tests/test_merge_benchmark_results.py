import json

from gamedevbench.src.utils.merge_benchmark_results import merge_results


def test_merge_results_replaces_exact_manifest_and_recomputes_summary(tmp_path):
    baseline_path = tmp_path / "baseline.json"
    replacement_path = tmp_path / "replacement.json"
    manifest_path = tmp_path / "tasks.yaml"
    output_path = tmp_path / "merged" / "final_results.json"
    baseline_path.write_text(
        json.dumps(
            {
                "configuration": {"model": "muse-spark-1.2"},
                "tasks": [
                    {"task_name": "task_0001", "success": False, "total_tokens": 1},
                    {"task_name": "task_0002", "success": True, "total_tokens": 1},
                ],
            }
        )
    )
    replacement_path.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "task_name": "task_0001",
                        "success": True,
                        "total_tokens": 3,
                        "solver_message": "complete",
                    }
                ]
            }
        )
    )
    manifest_path.write_text("tasks:\n  - task_0001\n")

    merged = merge_results(
        baseline_path,
        replacement_path,
        manifest_path,
        output_path,
        run_name="merged-run",
        effort="xhigh",
    )

    assert merged["success"] == 2
    assert merged["task_success_rate"] == 100.0
    assert merged["total_tasks_ran"] == 2
    assert merged["configuration"]["replacement_task_count"] == 1
    assert merged["tasks"][0]["original_result_replaced"] is True
    assert merged["tasks"][1]["original_result_replaced"] is False
    assert json.loads(output_path.read_text()) == merged
