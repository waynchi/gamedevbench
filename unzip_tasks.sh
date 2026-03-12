#!/bin/bash
# Unzips all individual task zips from tasks/ and tasks_gt/ into their respective directories.

set -e

for zip in tasks/task_*.zip; do
    unzip -q "$zip" -d tasks/
done
echo "Unzipped $(ls tasks/task_*.zip | wc -l | tr -d ' ') tasks to tasks/"

for zip in tasks_gt/task_*.zip; do
    unzip -q "$zip" -d tasks_gt/
done
echo "Unzipped $(ls tasks_gt/task_*.zip | wc -l | tr -d ' ') tasks to tasks_gt/"
