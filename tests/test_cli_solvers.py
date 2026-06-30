import json

import pytest

from gamedevbench.src.claude_code_solver import ClaudeCodeSolver
from gamedevbench.src.benchmark_runner import (
    MAX_TRAJECTORY_STREAM_CHARS,
    _write_solver_trajectory,
)
from gamedevbench.src.opencode_solver import OpenCodeSolver
from gamedevbench.src.omo_solver import OmoSolver
from gamedevbench.src.pi_solver import PiSolver
from gamedevbench.src.pi_stock_solver import PiStockSolver
from gamedevbench.src.utils.data_types import SolverResult, TokenUsage
from gamedevbench.src.utils.environment import collect_environment_metadata


def test_solver_trajectory_is_written_as_utf8(tmp_path):
    result = SolverResult(
        success=True,
        message="完成 ✓",
        duration_seconds=1.0,
        stdout='{"text":"中文 ✓"}',
        stderr="警告 ✓",
    )
    path = tmp_path / "agent_trajectory.log"

    _write_solver_trajectory(
        path,
        "task_utf8",
        "pi",
        "deepseek-v4-flash",
        tmp_path,
        result,
    )

    content = path.read_bytes().decode("utf-8")
    assert "完成 ✓" in content
    assert "中文 ✓" in content
    assert "警告 ✓" in content


def test_solver_trajectory_truncates_oversized_streams(tmp_path):
    stdout = "START✓" + ("中" * MAX_TRAJECTORY_STREAM_CHARS) + "END✓"
    result = SolverResult(
        success=True,
        message="done",
        duration_seconds=1.0,
        stdout=stdout,
    )
    path = tmp_path / "agent_trajectory.log"

    _write_solver_trajectory(
        path,
        "task_large",
        "pi",
        "deepseek-v4-flash",
        tmp_path,
        result,
    )

    content = path.read_text(encoding="utf-8")
    assert "START✓" in content
    assert "END✓" in content
    assert "omitted" in content
    assert path.stat().st_size < 16_000_000


def test_environment_metadata_records_versions(monkeypatch):
    monkeypatch.setattr(
        "gamedevbench.src.utils.environment._command_version",
        lambda command: "test-version" if command else None,
    )
    monkeypatch.setattr(
        "gamedevbench.src.utils.environment._git_metadata",
        lambda repo: {"commit": "abc", "branch": "test", "dirty": False},
    )

    metadata = collect_environment_metadata("pi")

    assert metadata["capture_source"] == "runtime"
    assert metadata["agent_cli"] == {"name": "pi", "version": "test-version"}
    assert metadata["godot"]["version"] == "test-version"
    assert metadata["gamedevbench"]["commit"] == "abc"


def test_pi_stock_uses_isolated_config_without_system_prompt(tmp_path, monkeypatch):
    (tmp_path / "settings.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("GAMEDEVBENCH_PI_STOCK_CONFIG_DIR", str(tmp_path))

    solver = PiStockSolver(model="deepseek/deepseek-v4-flash")
    env = solver._command_environment()

    assert env["PI_CODING_AGENT_DIR"] == str(tmp_path)
    assert not (tmp_path / "SYSTEM.md").exists()


def test_omo_uses_sisyphus_and_isolated_opencode_config(tmp_path, monkeypatch):
    monkeypatch.setenv("GAMEDEVBENCH_OMO_XDG_CONFIG_HOME", str(tmp_path))

    solver = OmoSolver(model="opencode-go/deepseek-v4-flash")
    env = solver._command_environment()

    assert solver._agent_name() == "Sisyphus - ultraworker"
    assert env["XDG_CONFIG_HOME"] == str(tmp_path)
    assert env["OPENCODE_CONFIG_DIR"] == str(tmp_path / "opencode")
    assert env["OMO_SEND_ANONYMOUS_TELEMETRY"] == "0"
    assert env["OPENCODE_DISABLE_CLAUDE_CODE_SKILLS"] == "true"


def test_omo_extracts_subagent_session_and_exported_usage():
    output = json.dumps(
        {
            "type": "tool_use",
            "part": {
                "tool": "task",
                "state": {"metadata": {"sessionId": "ses_child"}},
            },
        }
    )
    exported = json.dumps(
        {
            "info": {
                "tokens": {
                    "input": 100,
                    "output": 20,
                    "reasoning": 5,
                    "cache": {"read": 40, "write": 2},
                }
            }
        }
    )

    assert OmoSolver._subagent_session_ids(output) == {"ses_child"}
    usage = OmoSolver._parse_exported_usage(exported)
    assert usage.input_tokens == 100
    assert usage.output_tokens == 25
    assert usage.total_tokens == 125
    assert usage.cache_read_tokens == 40
    assert usage.cache_write_tokens == 2


def test_claude_code_parses_result_usage_cost_and_actual_model():
    output = "\n".join(
        [
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"model": "deepseek-v4-flash"},
                }
            ),
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": "done",
                    "total_cost_usd": 99.0,
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 20,
                        "cache_read_input_tokens": 40,
                        "cache_creation_input_tokens": 5,
                    },
                }
            ),
        ]
    )

    parsed = ClaudeCodeSolver._parse_output(output)

    assert parsed["response"] == "done"
    assert parsed["model"] == "deepseek-v4-flash"
    assert parsed["token_usage"].input_tokens == 100
    assert parsed["token_usage"].output_tokens == 20
    assert parsed["token_usage"].cache_read_tokens == 40
    assert parsed["token_usage"].cache_write_tokens == 5


def test_opencode_parses_usage_response_and_reasoning_tokens():
    output = "\n".join(
        [
            json.dumps({"type": "text", "part": {"text": "done"}}),
            json.dumps(
                {
                    "type": "step_finish",
                    "part": {
                        "tokens": {
                            "input": 100,
                            "output": 20,
                            "reasoning": 30,
                            "cache": {"read": 40, "write": 5},
                        },
                        "cost": 99.0,
                    },
                }
            ),
        ]
    )

    parsed = OpenCodeSolver._parse_output(output)

    assert parsed["response"] == "done"
    assert parsed["token_usage"].input_tokens == 100
    assert parsed["token_usage"].output_tokens == 50
    assert parsed["token_usage"].total_tokens == 150
    assert parsed["token_usage"].cache_read_tokens == 40
    assert parsed["token_usage"].cache_write_tokens == 5


def test_opencode_treats_json_error_as_failure_detail():
    output = json.dumps(
        {
            "type": "error",
            "error": {"data": {"message": "rate limit exceeded"}},
        }
    )

    parsed = OpenCodeSolver._parse_output(output)

    assert parsed["error"] == "rate limit exceeded"


def test_pi_sums_assistant_turn_usage_and_uses_last_response():
    def assistant_message(text, input_tokens, output_tokens, cost):
        return json.dumps(
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": text}],
                    "model": "deepseek-v4-flash",
                    "usage": {
                        "input": input_tokens,
                        "output": output_tokens,
                        "cacheRead": 10,
                        "cacheWrite": 2,
                        "cost": {"total": cost},
                    },
                    "stopReason": "stop",
                },
            }
        )

    output = "\n".join(
        [
            assistant_message("working", 100, 20, 0.01),
            assistant_message("done", 200, 30, 0.02),
        ]
    )

    parsed = PiSolver._parse_output(output)

    assert parsed["response"] == "done"
    assert parsed["model"] == "deepseek-v4-flash"
    assert parsed["token_usage"].input_tokens == 300
    assert parsed["token_usage"].output_tokens == 50
    assert parsed["token_usage"].total_tokens == 350
    assert parsed["token_usage"].cache_read_tokens == 20
    assert parsed["token_usage"].cache_write_tokens == 4


def test_deepseek_v4_flash_cost_uses_opencode_go_token_pricing():
    usage = TokenUsage(
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_read_tokens=1_000_000,
        cache_write_tokens=1_000_000,
    )

    assert usage.calculate_cost("opencode-go/deepseek-v4-flash") == pytest.approx(
        0.4228
    )


def test_deepseek_v4_pro_cost_uses_opencode_go_token_pricing():
    usage = TokenUsage(
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_read_tokens=1_000_000,
        cache_write_tokens=1_000_000,
    )

    assert usage.calculate_cost("deepseek-v4-pro") == pytest.approx(5.2345)
