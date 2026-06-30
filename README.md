# GameDevBench: Evaluating Agentic Capabilities Through Game Development

Wayne Chi, Yixiong Fang, Arnav Yayavaram, Siddharth Yayavaram, Seth Karten,
Qiuhong Anna Wei, Runkun Chen, Alexander Wang, Valerie Chen, Ameet Talwalkar, Chris Donahue

*Carnegie Mellon University, Princeton University*

[![Project Page](https://img.shields.io/badge/Project-Page-blue.svg)](https://waynechi.com/gamedevbench)
[![arXiv](https://img.shields.io/badge/arXiv-2602.11103-b31b1b.svg)](https://arxiv.org/abs/2602.11103)
[![Hugging Face Paper](https://img.shields.io/badge/%F0%9F%A4%97%20Hugging%20Face-Paper-yellow)](https://huggingface.co/papers/2602.11103)
[![Godot](https://img.shields.io/badge/Godot-4.x-brightgreen.svg)](https://godotengine.org/)

**The first benchmark for evaluating LLM agents on game development tasks in a modern game engine.**

## Abstract

Despite rapid progress on coding agents, progress on their multimodal counterparts has lagged behind. A key challenge is the scarcity of evaluation testbeds that combine the complexity of software development with the need for deep multimodal understanding. Game development provides such a testbed as agents must navigate large, dense codebases while manipulating intrinsically multimodal assets such as shaders, sprites, and animations within a visual game scene. We present **GameDevBench**, the first benchmark for evaluating agents on game development tasks. GameDevBench consists of 132 tasks derived from web and video tutorials. Tasks require significant multimodal understanding and are complex — the average solution requires over three times the amount of lines of code and file changes compared to prior software development benchmarks. Agents still struggle with game development, with the best agent solving only 54.5% of tasks. We find a strong correlation between perceived task difficulty and multimodal complexity, with success rates dropping from 46.9% on gameplay-oriented tasks to 31.6% on 2D graphics tasks. To improve multimodal capability, we introduce two simple image and video-based feedback mechanisms for agents. Despite their simplicity, these methods consistently improve performance, with the largest change being an increase in Claude Sonnet 4.5's performance from 33.3% to 47.7%. We release GameDevBench publicly to support further research into agentic game development.

<p align="center">
  <img src="assets/taxonomy-examples.png" alt="GameDevBench task taxonomy" width="90%">
</p>

## Overview

GameDevBench contains **132 game development tasks** to evaluate LLM agents' ability to complete game development problems in the **Godot game engine**. Tasks span four categories — 3D Graphics, 2D Graphics, Gameplay, and UI — and require agents to reason about multimodal assets including shaders, sprites, animations, and visual game scenes.

<p align="center">
  <img src="assets/example_workflow.png" alt="GameDevBench example workflow" width="90%">
</p>

## Installation

#### Prerequisites

1. **Godot 4.x** — Download and install from [godotengine.org](https://godotengine.org/download)
   - Ensure `godot` is available in your PATH, or set `GODOT_EXEC_PATH` environment variable

2. **Python 3.10+** — Required for all agents
   - **Python 3.12+** — Required for OpenHands agent

#### Install Agents

Install the agent(s) you want to use:

- **Claude Code** — [Claude Code](https://code.claude.com/docs/en/overview)
- **Codex** — [Codex](https://openai.com/codex/)
- **Gemini CLI** — [Gemini CLI](https://geminicli.com/)
- **OpenHands** — [OpenHands](https://www.openhands.dev/)
- **OpenCode** — [OpenCode](https://opencode.ai/)
- **Pi** — [Pi coding agent](https://github.com/badlogic/pi-mono)

#### Setup Tasks

Before running the benchmark, unzip the tasks:

```bash
bash unzip_tasks.sh
```

This will unzip all individual task archives from `tasks/` and `tasks_gt/` in place.

> Tasks are distributed as individual zip files to prevent accidental data leakage.

## Configuration

You can use the built-in plans for `claude-code`, `codex`, and `gemini-cli`, or provide API keys directly. For OpenHands you must provide your own API keys. See [`.env.example`](.env.example) for a complete list of optional environment variables.

## Usage

```bash
uv run python gamedevbench/src/benchmark_runner.py \
  --agent AGENT \
  --model MODEL \
  run --task-list tasks.yaml
```

#### Available Agents

| Agent | Description |
|-------|-------------|
| `claude-code` | Anthropic's Claude Code CLI |
| `codex` | OpenAI Codex |
| `gemini-cli` | Google Gemini CLI |
| `openhands` | OpenHands (requires Python 3.12+) |
| `opencode` | OpenCode CLI, using its default agent prompt and tools |
| `omo` | Oh My OpenAgent's Sisyphus orchestrator in an isolated OpenCode config |
| `pi` | Pi coding agent, using its default prompt, tools, and extensions |
| `pi-stock` | Pi with an isolated config that omits the user's `SYSTEM.md` |

OpenCode and Pi use different provider-qualified model names. For example, the
same DeepSeek V4 Flash model may be selected as
`opencode-go/deepseek-v4-flash` in OpenCode and
`deepseek/deepseek-v4-flash` in Pi. Verify the available names with
`opencode models` and `pi --list-models deepseek` before a run.

#### Isolated Pi and OMO Variants

Prepare both isolated configurations without modifying the user's normal Pi or
OpenCode configuration:

```powershell
.\prepare_benchmark_variants.ps1
```

The OMO configuration pins `oh-my-openagent@4.10.0`, disables model fallback,
and maps all built-in agents and categories to
`opencode-go/deepseek-v4-flash`. Run the paired 24-task comparison with:

```powershell
.\run_benchmark_variants.ps1
```

#### Command-Line Options

| Option | Description |
|--------|-------------|
| `--agent AGENT` | Agent to use (required) |
| `--model MODEL` | Model name (e.g., `claude-sonnet-4.5-20250929`) |
| `--enable-mcp` | Enable MCP server for screenshot capabilities |
| `--use-runtime-video` | Enable runtime video mode with Godot runtime instructions |
| `--skip-display` | Skip tasks that require display |
| `run --task-list FILE` | Run tasks from YAML file (e.g., `tasks.yaml`) |

## Platform Limitations

- MCP server screenshot functionality (`--enable-mcp`) currently only works on **macOS**
  - Uses AppleScript for display capture
  - Requires setting `GODOT_SCREENSHOT_DISPLAY` environment variable to correct display number

## Results

Benchmark results are saved to `results/` directory with the following information:
- Task success/failure status
- Token usage and costs
- Execution time
- Validation results
- Environment metadata, including agent CLI, Godot, Python, and repository versions

### Local 24-Task Agent Harness Comparison

The following local comparison used the same fixed 24-task subset,
`benchmark_24.yaml`, across agent harnesses. Each run used the normal
GameDevBench validation loop in Godot 4.6.2 and recorded token/cost data from
the agent harness output. Runs were kept in separate result directories and
were not merged with the upstream benchmark leaderboard.

Unless noted otherwise, reasoning was configured through the corresponding
agent's normal settings rather than explicit benchmark CLI flags. Claude Code
was run with high effort; OpenCode and OMO were run with medium effort; Pi
runs used high thinking from Pi's `defaultThinkingLevel`; the Codex baseline
used the configured default model with medium reasoning.

| Run | Agent / Harness | Model or routing | Reasoning | Passed | Success rate | Tokens | Cost | Notes |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| DeepSeek V4 Pro practical | Claude Code | `deepseek-v4-pro` with configured Claude Code sub-model routing | High | 11 / 24 | 45.83% | 3,361,199 | $0.5635 | Claude Code routed most recorded task calls to the configured fast sub-model, which affects the cost comparison. |
| DeepSeek V4 Pro practical | OpenCode | `opencode-go/deepseek-v4-pro` | Medium | 8 / 24 | 33.33% | 654,711 | $1.4755 | Standard OpenCode harness. |
| DeepSeek V4 Pro practical | Pi | `deepseek/deepseek-v4-pro` | High | 9 / 24 | 37.50% | 440,759 | $1.0659 | Pi run using the user's replacement system prompt. |
| DeepSeek V4 Pro practical | Pi stock | `deepseek/deepseek-v4-pro` | High | 7 / 24 | 29.17% | 401,934 | $1.0565 | Isolated Pi config without the user's replacement system prompt; one runner error was recorded. |
| DeepSeek V4 Pro practical | OMO | Mixed `deepseek-v4-pro` / `deepseek-v4-flash` role routing | Medium | 6 / 24 | 25.00% | 1,190,474 | $2.4842 | Practical mixed setup: orchestration and high-impact roles used Pro, lightweight/support roles used Flash. |
| Codex baseline | Codex | Configured default `gpt-5.5` | Medium | 14 / 24 | 58.33% | 4,584,125 | $14.7274 | Run without an explicit `--model`; cost uses local runner pricing metadata and may not match actual billing. |
| Pi GLM-5.2 | Pi | `opencode_go/glm-5.2` | High | 8 / 24 | 33.33% | 379,584 | $2.2055 | Three connection-error tasks were rerun and merged into the run; six zero-token timeout/error tasks remained. |
| Pi Qwen3.7 Max | Pi | `opencode_go/qwen3.7-max` | High | 11 / 24 | 45.83% | 765,477 | $3.7130 | One runner error (`task_0109`) was recorded. |

The 24-task subset used for these local runs was:

`task_0001`, `task_0007`, `task_0012`, `task_0018`, `task_0024`,
`task_0029`, `task_0035`, `task_0041`, `task_0047`, `task_0052`,
`task_0058`, `task_0064`, `task_0069`, `task_0075`, `task_0081`,
`task_0086`, `task_0092`, `task_0098`, `task_0104`, `task_0109`,
`task_0115`, `task_0121`, `task_0126`, and `task_0132`.

## Citation

If you find GameDevBench useful, please cite our paper:

```bibtex
@misc{chi2026gamedevbenchevaluatingagenticcapabilities,
      title={GameDevBench: Evaluating Agentic Capabilities Through Game Development},
      author={Wayne Chi and Yixiong Fang and Arnav Yayavaram and Siddharth Yayavaram and Seth Karten and Qiuhong Anna Wei and Runkun Chen and Alexander Wang and Valerie Chen and Ameet Talwalkar and Chris Donahue},
      year={2026},
      eprint={2602.11103},
      archivePrefix={arXiv},
      primaryClass={cs.AI},
      url={https://arxiv.org/abs/2602.11103},
}
```

## License
