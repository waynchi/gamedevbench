#!/usr/bin/env python3
"""Validate GameDevBench ground-truth solutions in parallel.

Runs `gamedevbench --gt validate <task>` for every task in tasks.yaml and
reports a summary. Every ground truth is expected to PASS; use this to verify
your setup (Godot install, unzipped tasks) or the integrity of a release.

Usage:
    uv run python validate_tasks.py                 # all tasks, parallel
    uv run python validate_tasks.py --workers 4
    uv run python validate_tasks.py --task-list tasks.yaml
    uv run python validate_tasks.py --tasks task_0002 task_0021

Requires tasks to be unzipped first (bash unzip_tasks.sh) and Godot 4.4.1
available (in PATH or via GODOT_EXEC_PATH).

Per-task results are written by the harness to results/task_task_<name>.json;
a summary is written to results/gt_validation_summary.json. Tasks that time
out (e.g. cold-start import on first run) are retried once.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import yaml

from gamedevbench.src.utils.godot_version import (
    GodotVersionError,
    require_supported_godot,
)

REPO = Path(__file__).resolve().parent
RESULTS = REPO / "results"
RUN_TIMEOUT = 700  # harness enforces 600s internally


GODOT = os.environ.get("GODOT_EXEC_PATH", "godot")


def validate_one(task: str):
    start = time.time()
    result_file = RESULTS / f"task_{task}.json"
    result_file.unlink(missing_ok=True)  # never read a stale result
    # Import resources first: the harness's brief editor warmup is not long
    # enough for asset-heavy projects, especially under parallel load.
    # No-op when the .godot cache is already present and current.
    try:
        subprocess.run(
            [GODOT, "--headless", "--import", "--path", str(REPO / "tasks_gt" / task)],
            capture_output=True, timeout=300,
        )
    except (subprocess.TimeoutExpired, OSError):
        pass  # validation below will surface any real problem
    try:
        proc = subprocess.run(
            ["uv", "run", "--no-sync", "gamedevbench", "--gt", "validate", task],
            capture_output=True, text=True, timeout=RUN_TIMEOUT, cwd=REPO,
        )
    except subprocess.TimeoutExpired:
        return task, False, "subprocess timeout", time.time() - start
    if result_file.exists():
        data = json.loads(result_file.read_text())
        return task, bool(data.get("success")), data.get("message", ""), time.time() - start
    return task, False, f"no result file (exit {proc.returncode})", time.time() - start


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-list", default=str(REPO / "tasks.yaml"))
    ap.add_argument("--tasks", nargs="*", help="explicit task names (overrides --task-list)")
    ap.add_argument("--workers", type=int,
                    default=max(1, min(8, (os.cpu_count() or 4) - 2)))
    args = ap.parse_args()

    try:
        godot_version = require_supported_godot(GODOT)
    except GodotVersionError as error:
        ap.error(str(error))
    os.environ["GODOT_EXEC_PATH"] = GODOT
    print(f"Using Godot {godot_version} ({GODOT})")

    tasks = args.tasks or yaml.safe_load(open(args.task_list))["tasks"]
    print(f"Validating {len(tasks)} ground truths with {args.workers} workers")

    # warm up: sync the venv once before parallel --no-sync runs
    subprocess.run(["uv", "run", "python", "-c", "pass"], cwd=REPO,
                   capture_output=True)

    results = {}
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(validate_one, t): t for t in tasks}
        for i, fut in enumerate(as_completed(futures), 1):
            task, ok, msg, dur = fut.result()
            results[task] = (ok, msg, dur)
            status = "PASS" if ok else "FAIL"
            print(f"[{i}/{len(tasks)}] {status} {task} ({dur:.1f}s)"
                  + ("" if ok else f" - {msg}"), flush=True)

    # retry failures once, sequentially (timeouts are usually cold-start)
    failed = [t for t, (ok, _, _) in results.items() if not ok]
    if failed:
        print(f"\nRetrying {len(failed)} failed task(s) sequentially...")
        for t in failed:
            task, ok, msg, dur = validate_one(t)
            results[task] = (ok, msg, dur)
            print(f"retry {'PASS' if ok else 'FAIL'} {task} ({dur:.1f}s)"
                  + ("" if ok else f" - {msg}"), flush=True)

    passed = sum(1 for ok, _, _ in results.values() if ok)
    failed = sorted(t for t, (ok, _, _) in results.items() if not ok)
    summary = {
        "total": len(tasks),
        "passed": passed,
        "failed": failed,
        "elapsed_seconds": round(time.time() - t0, 1),
    }
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "gt_validation_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n")
    print(f"\n{passed}/{len(tasks)} ground truths passed "
          f"in {summary['elapsed_seconds']}s")
    if failed:
        print("FAILED:", *failed, sep="\n  ")
        sys.exit(1)
    print("ALL GROUND TRUTHS PASS")


if __name__ == "__main__":
    main()
