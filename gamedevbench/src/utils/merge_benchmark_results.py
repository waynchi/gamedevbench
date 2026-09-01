#!/usr/bin/env python3
"""Merge selected benchmark reruns into a full-run results JSON."""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path

import yaml


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _task_map(results: dict, label: str) -> dict[str, dict]:
    tasks = results.get("tasks")
    if not isinstance(tasks, list):
        raise ValueError(f"{label} does not contain a tasks list")
    mapped = {task.get("task_name"): task for task in tasks}
    if None in mapped or len(mapped) != len(tasks):
        raise ValueError(f"{label} contains missing or duplicate task names")
    return mapped


def _selected_tasks(manifest: Path) -> set[str]:
    data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
    tasks = data.get("tasks", []) if isinstance(data, dict) else data
    if not isinstance(tasks, list) or not all(isinstance(task, str) for task in tasks):
        raise ValueError("manifest must contain a string task list")
    selected = set(tasks)
    if len(selected) != len(tasks):
        raise ValueError("manifest contains duplicate tasks")
    return selected


def _summary(tasks: list[dict], configuration: dict) -> dict:
    success = sum(task.get("success") is True for task in tasks)
    skipped = sum(task.get("skipped") is True for task in tasks)
    attempted = len(tasks) - skipped
    tasks_with_data = [task for task in tasks if task.get("total_tokens", 0) > 0]

    def total(field: str, default=0):
        return sum(task.get(field, default) or default for task in tasks)

    divisor = len(tasks_with_data)
    input_tokens = total("input_tokens")
    output_tokens = total("output_tokens")
    all_tokens = total("total_tokens")
    cache_read = total("cache_read_tokens")
    cache_write = total("cache_write_tokens")
    cost = total("cost_usd", 0.0)
    duration = total("solver_duration", 0.0)
    timeout_count = sum(
        (task.get("solver_message") or "").startswith("Muse execution timed out after")
        for task in tasks
    )

    return {
        "success": success,
        "failures": len(tasks) - success - skipped,
        "errors": 0,
        "skipped": skipped,
        "total_tasks_ran": len(tasks),
        "tasks_attempted": attempted,
        "task_success_rate": round(success / attempted * 100, 2) if attempted else 0.0,
        "timeout_count": timeout_count,
        "timestamp": datetime.now().isoformat(),
        "configuration": configuration,
        "token_statistics": {
            "total_input_tokens": input_tokens,
            "total_output_tokens": output_tokens,
            "total_tokens": all_tokens,
            "total_cache_read_tokens": cache_read,
            "total_cache_write_tokens": cache_write,
            "avg_input_tokens": round(input_tokens / divisor, 2) if divisor else 0,
            "avg_output_tokens": round(output_tokens / divisor, 2) if divisor else 0,
            "avg_tokens_per_task": round(all_tokens / divisor, 2) if divisor else 0,
            "avg_cache_read_tokens": round(cache_read / divisor, 2) if divisor else 0,
            "avg_cache_write_tokens": round(cache_write / divisor, 2) if divisor else 0,
        },
        "cost_statistics": {
            "total_cost_usd": round(cost, 4),
            "avg_cost_per_task_usd": round(cost / divisor, 4) if divisor else 0.0,
        },
        "duration_statistics": {
            "total_duration_seconds": round(duration, 2),
            "avg_duration_per_task_seconds": round(duration / divisor, 2) if divisor else 0.0,
        },
        "rate_limited": any(task.get("is_rate_limited") for task in tasks),
        "tasks": tasks,
    }


def merge_results(
    baseline_path: Path,
    replacement_path: Path,
    manifest_path: Path,
    output_path: Path,
    *,
    run_name: str,
    effort: str,
) -> dict:
    baseline = _load_json(baseline_path)
    replacement = _load_json(replacement_path)
    baseline_tasks = _task_map(baseline, "baseline")
    replacement_tasks = _task_map(replacement, "replacement")
    selected = _selected_tasks(manifest_path)

    if set(replacement_tasks) != selected:
        missing = sorted(selected - set(replacement_tasks))
        unexpected = sorted(set(replacement_tasks) - selected)
        raise ValueError(
            f"replacement set does not match manifest: missing={missing}, "
            f"unexpected={unexpected}"
        )
    if not selected <= set(baseline_tasks):
        raise ValueError(
            f"replacement tasks absent from baseline: {sorted(selected - set(baseline_tasks))}"
        )

    tasks = []
    for baseline_task in baseline["tasks"]:
        task_name = baseline_task["task_name"]
        if task_name in selected:
            task = deepcopy(replacement_tasks[task_name])
            task["result_origin"] = str(replacement_path)
            task["baseline_result_origin"] = str(baseline_path)
            task["original_result_replaced"] = True
        else:
            task = deepcopy(baseline_task)
            task.setdefault("result_origin", str(baseline_path))
            task.setdefault("original_result_replaced", False)
        tasks.append(task)

    configuration = deepcopy(baseline.get("configuration", {}))
    configuration.update(
        {
            "run_name": run_name,
            "effort": effort,
            "confinement": "mixed_provenance_strict_replacements",
            "replacement_task_count": len(selected),
            "solver_timeout_seconds": 3600,
        }
    )
    merged = _summary(tasks, configuration)
    merged["merge_provenance"] = {
        "baseline": str(baseline_path),
        "replacement": str(replacement_path),
        "manifest": str(manifest_path),
        "replacement_task_count": len(selected),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    return merged


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--replacement", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--run-name", required=True)
    parser.add_argument("--effort", required=True)
    args = parser.parse_args()
    merged = merge_results(
        args.baseline,
        args.replacement,
        args.manifest,
        args.output,
        run_name=args.run_name,
        effort=args.effort,
    )
    print(
        f"Merged {merged['configuration']['replacement_task_count']} replacements: "
        f"{merged['success']}/{merged['tasks_attempted']} passed, "
        f"{merged['timeout_count']} timeouts -> {args.output}"
    )


if __name__ == "__main__":
    main()
