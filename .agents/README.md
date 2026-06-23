# Agent Setup Notes

This repo is GameDevBench, a Python benchmark runner for Godot tasks. Use this file as the bootstrap checklist before editing or running benchmarks.

## Repository Rules

- Work on a branch. Do not commit directly to `main` unless the user explicitly asks.
- Do not commit benchmark outputs from `results/`, `tasks/test_result/`, or temporary Godot sandboxes.
- Prefer `uv run ...` for Python entry points so the repo environment is used consistently.
- Use `rg` for searches.

## Fresh Checkout Setup

Install system tools:

```bash
git lfs install
git lfs pull
uv sync
```

Install Godot 4.4.x and make sure `godot` is on `PATH`:

```bash
godot --version
```

Unpack the task archives. This command is safe to rerun:

```bash
bash unzip_tasks.sh
```

Verify that the repository can validate ground-truth tasks:

```bash
uv run gamedevbench --gt validate task_0002
uv run python validate_tasks.py
```

The full `validate_tasks.py` check validates all 333 ground-truth tasks and can take a while.

## OpenCode GLM 5.2 Setup

Install OpenCode:

```bash
npm install -g opencode-ai
opencode --version
```

Authenticate the Z.AI coding plan provider:

```bash
opencode auth login
opencode auth list
opencode models zai-coding-plan
```

The repo-level `opencode.json` is the source of truth for OpenCode command-line behavior. It configures:

- `zai-coding-plan/glm-5.2`
- the `build` agent
- max thinking via `"variant": "max"`
- permissive benchmark execution permissions
- provider timeout options

Do not pass model, agent, or permission flags directly to `opencode run` for benchmark runs unless the config is intentionally changed.

Smoke-test OpenCode from the repo:

```bash
OPENCODE_CONFIG="$PWD/opencode.json" opencode run --format json --dir "$PWD" "Reply with exactly ok"
```

## Running Benchmarks

Headless GLM 5.2 run through OpenCode:

```bash
uv run gamedevbench \
  --agent opencode \
  --run-name glm52_opencode_full_headless \
  --skip-display \
  --parallel 2 \
  run --task-list tasks.yaml
```

Resume an interrupted run:

```bash
uv run gamedevbench \
  --agent opencode \
  --run-name glm52_opencode_full_headless \
  --skip-display \
  --resume \
  --parallel 2 \
  run --task-list tasks.yaml
```

Parallelism is supported for task-list and all-task runs. Start at `--parallel 2` for OpenCode GLM 5.2, then increase only if the provider is stable.

## Useful Checks

Compile runner and solver modules after edits:

```bash
uv run python -m py_compile gamedevbench/src/benchmark_runner.py gamedevbench/src/opencode_solver.py
```

Run a cheap parallel validation smoke without spending model calls:

```bash
uv run gamedevbench --gt --run-name parallel_validation_smoke --parallel 2 run --task-list test_task.yaml
```

Check for lingering benchmark processes before restarting a run:

```bash
pgrep -af "opencode|gamedevbench|benchmark_runner|godot" || true
```
