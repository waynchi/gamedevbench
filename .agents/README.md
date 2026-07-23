# Agent Setup Notes

This repo is GameDevBench, a Python benchmark runner for Godot tasks. Use this file as the bootstrap checklist before editing or running benchmarks.

## Repository Rules

- Work on a branch. Do not commit directly to `main` unless the user explicitly asks.
- Do not commit per-task benchmark outputs, trajectories, sandbox code, `tasks/test_result/`, or temporary Godot sandboxes. Only commit aggregate `final_results.json` files when the user explicitly asks for benchmark results to be tracked.
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

For MCP-enabled OpenCode runs, `opencode.mcp.json` is used instead. It keeps the same GLM 5.2 build-agent settings and enables the local `gamedevbench-mcp` server.

Smoke-test OpenCode from the repo:

```bash
OPENCODE_CONFIG="$PWD/opencode.json" opencode run --format json --dir "$PWD" "Reply with exactly ok"
```

## OpenHands GLM 5.2 Setup

OpenHands requires Python 3.12+ in this repo because `openhands-sdk` and `openhands-tools` are only installed for Python 3.12 or newer:

```bash
uv run --python 3.12 python --version
uv run python - <<'PY'
from gamedevbench.src.solver_factory import SolverFactory
print(SolverFactory.get_available_agents())
PY
```

The second command should include `openhands`.

OpenHands uses LiteLLM model names and environment variables, not `opencode.json`. Configure the provider before starting a benchmark. For OpenRouter-style access, set:

```bash
export OPENROUTER_API_KEY="..."
export OPENROUTER_API_BASE="https://openrouter.ai/api/v1"
export OR_SITE_URL="https://github.com/waynechi/gamedevbench"
export OR_APP_NAME="GameDevBench"
```

OpenRouter lists GLM 5.2 as `z-ai/glm-5.2`, so the LiteLLM/OpenHands model string is `openrouter/z-ai/glm-5.2`. For example:

```bash
uv run gamedevbench \
  --agent openhands \
  --model openrouter/z-ai/glm-5.2 \
  --run-name glm52_openhands_full \
  --parallel 2 \
  run --task-list tasks.yaml
```

The OpenRouter model page is https://openrouter.ai/z-ai/glm-5.2. Do not reuse the OpenCode model string `zai-coding-plan/glm-5.2` for OpenHands unless LiteLLM can route that exact provider/model and the matching API key is configured. If OpenHands is run without a matching provider key, tasks fail immediately with messages such as `OPENAI_API_KEY environment variable not set`.

## Running Benchmarks

GLM 5.2 run through OpenCode:

```bash
uv run gamedevbench \
  --agent opencode \
  --run-name glm52_opencode_full \
  --parallel 2 \
  run --task-list tasks.yaml
```

Resume an interrupted run:

```bash
uv run gamedevbench \
  --agent opencode \
  --run-name glm52_opencode_full \
  --resume \
  --parallel 2 \
  run --task-list tasks.yaml
```

Parallelism is supported for task-list and all-task runs. Start at `--parallel 2` for OpenCode GLM 5.2, then increase only if the provider is stable.

The default solver timeout is 600 seconds / 10 minutes. Use `--solver-timeout <seconds>` to override it for a run, or `--solver-timeout 0` to disable only the solver timeout. Validation still uses the benchmark's built-in validation timeout.

## Reasoning Effort

Use the generic `--effort <value>` option for Claude Code, Codex, OpenHands,
and OpenCode runs. The runner maps it to each harness's native setting and
records the value in result metadata. Accepted values depend on the provider
and model; common values are `low`, `medium`, `high`, and `xhigh`.

Omit `--effort` for baseline runs that should retain the harness configuration
or server-side default. Do not infer or label an unset default as a specific
effort level without separate request-level verification.

GLM 5.2 run with both runtime-video prompt guidance and MCP enabled:

```bash
uv run gamedevbench \
  --agent opencode \
  --run-name glm52_opencode_runtime_video_mcp \
  --use-runtime-video \
  --enable-mcp \
  --parallel 2 \
  run --task-list tasks.yaml
```

Resume that run:

```bash
uv run gamedevbench \
  --agent opencode \
  --run-name glm52_opencode_runtime_video_mcp \
  --use-runtime-video \
  --enable-mcp \
  --resume \
  --parallel 2 \
  run --task-list tasks.yaml
```

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
