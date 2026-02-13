# GameDevBench

A benchmark suite for evaluating LLM agents on game development tasks.

**Paper:** [GameDevBench: A Comprehensive Benchmark for Game Development](https://arxiv.org/abs/2602.11103)

## Overview

GameDevBench contains 132 game development tasks to evaluate LLM agents' ability to complete game development problems in the Godot game engine.

## Installation

### Prerequisites

1. **Godot 4.x** - Download and install from [godotengine.org](https://godotengine.org/download)
   - Ensure `godot` is available in your PATH, or set `GODOT_EXEC_PATH` environment variable

2. **Python 3.10+** - Required for all agents
   - **Python 3.12+** - Required for OpenHands agent

### Install Agents

Install the agent(s) you want to use:

- **Claude Code** - [Claude Code](https://code.claude.com/docs/en/overview)
- **Codex** - [Codex](https://openai.com/codex/)
- **Gemini CLI** - [Gemini CLI](https://geminicli.com/)
- **OpenHands** - [OpenHands](https://www.openhands.dev/)

### Setup Tasks

Before running the benchmark, unzip the tasks folder:

```bash
unzip tasks.zip
```

**Note:** The tasks are distributed as a zip file to prevent accidental data leakage.

## Configuration

### Environment Variables

You can use the built-in plans for `claude-code`, `codex`, and `gemini-cli`, or provide API keys directly. For OpenHands you must provide your own API keys. See `.env.example` for a complete list of optional environment variables.

## Usage

### Running the Benchmark

```bash
uv run python gamedevbench/src/benchmark_runner.py \
  --agent AGENT \
  --model MODEL \
  run --task-list tasks.yaml
```

#### Available Agents

- `claude-code` - Anthropic's Claude Code CLI
- `codex` - OpenAI Codex
- `gemini-cli` - Google Gemini CLI
- `openhands` - OpenHands (requires Python 3.12+)

#### Command-Line Options

- `--agent AGENT` - Agent to use (required)
- `--model MODEL` - Model name (e.g., `claude-sonnet-4.5-20250929`)
- `--enable-mcp` - Enable MCP (Model Context Protocol) server for supported agents
  - Provides screenshot capabilities to the agent
  - **Note:** MCP server requires macOS (see limitations below)
- `--use-runtime-video` - Enable runtime video mode
  - Appends Godot runtime instructions to prompts
  - Helps agents understand how to run and test their changes
- `--skip-display` - Skip tasks that require display
- `run --task-list FILE` - Run tasks from YAML file (e.g., `tasks.yaml`)

## Platform Limitations

**macOS-only Features:**
- MCP server screenshot functionality (`--enable-mcp`) currently only works on macOS
  - Uses AppleScript for display capture
  - Requires setting `GODOT_SCREENSHOT_DISPLAY` environment variable to correct display number

## Results

Benchmark results are saved to `results/` directory with the following information:
- Task success/failure status
- Token usage and costs
- Execution time
- Validation results

## Citation

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
