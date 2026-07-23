import json

from gamedevbench.src.benchmark_runner import GodotBenchmarkRunner


def test_results_resume_only_redoes_selected_incomplete_tasks(tmp_path):
    results_path = tmp_path / "final_results.json"
    results_path.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "task_name": "task_outside_selection",
                        "solver_success": False,
                    },
                    {
                        "task_name": "task_selected_complete",
                        "solver_success": True,
                    },
                    {
                        "task_name": "task_selected_incomplete",
                        "solver_success": False,
                    },
                ]
            }
        )
    )
    runner = GodotBenchmarkRunner(use_gt=False)

    tasks_to_skip, tasks_to_redo, previous_results = (
        runner._load_results_from_file(
            str(results_path),
            selected_tasks={
                "task_selected_complete",
                "task_selected_incomplete",
            },
        )
    )

    assert tasks_to_skip == [
        "task_outside_selection",
        "task_selected_complete",
    ]
    assert tasks_to_redo == ["task_selected_incomplete"]
    assert [
        result["task_name"] for result in previous_results
    ] == tasks_to_skip


def test_task_list_is_passed_as_resume_scope(monkeypatch):
    runner = GodotBenchmarkRunner(
        use_gt=False,
        resume_from="final_results.json",
    )
    monkeypatch.setattr(
        runner,
        "load_tasks_from_file",
        lambda path: ["task_selected"],
    )
    captured = {}

    def fake_load_results(path, selected_tasks=None):
        captured["selected_tasks"] = selected_tasks
        return (
            ["task_selected"],
            [],
            [{"task_name": "task_selected", "solver_success": True}],
        )

    monkeypatch.setattr(runner, "_load_results_from_file", fake_load_results)
    monkeypatch.setattr(runner, "_save_final_results", lambda *args: None)

    runner.run_all_tasks("selection.yaml")

    assert captured["selected_tasks"] == {"task_selected"}
