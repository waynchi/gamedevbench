#!/usr/bin/env python3
"""
Centralized prompt creation for gamedev benchmark solvers.

This module provides unified prompt creation functions used by all solver implementations,
ensuring consistency across different agents.
"""

import json
from typing import Optional


def load_task_config() -> Optional[dict]:
    """Load task configuration from task_config.json in current directory.

    Returns:
        Parsed task configuration dict, or None if loading fails
    """
    try:
        with open("task_config.json", "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading config: {e}")
        return None


def create_task_prompt(config: dict, use_runtime_video: bool = False, use_mcp: bool = False) -> str:
    """Create minimal task prompt with just the instruction.

    Args:
        config: Task configuration dict containing 'instruction' field
        use_runtime_video: Whether to append Godot runtime video instructions
        use_mcp: Whether to include MCP tool references

    Returns:
        The instruction text with optional runtime video and MCP guidance
    """
    try:
        if not config or "instruction" not in config:
            raise ValueError("Invalid config: 'instruction' field missing")
    except Exception as e:
        print(f"Error creating task prompt: {e}")
        return ""
    instruction = config.get("instruction")
    
    instruction += "\n You must complete the full task without any further assistance."
    instruction += "\n Godot is installed and you can run godot using the `godot` command. It is recommended to run this with a timeout (e.g., `timeout 10 godot` for 10 second timeout) to prevent hanging."
    instruction += "You are a visual agent and can use images and videos to help you understand the state of the game."

    if use_runtime_video:
        runtime_guidance = """
    - You can run the game and get an image with `godot --path . --quit-after 1
    --write-movie output.png`.
    - You can save a movie file as avi instead with `timeout 60s godot --path . --quit-after 60 --write-movie output.avi`. This is a 1 second or 60 frame video. You can adjust as necessary.
    - It is very important that you ensure godot closes after running, or else the task will hang indefinitely.
    - You should use the video or images to verify that your changes worked as expected.
    """
        instruction += runtime_guidance

    if use_mcp:
        mcp_guidance = """

You have access to a Godot MCP (Model Context Protocol) server that provides specialized tools for working with Godot projects.

Available MCP Tools:
- `godot-screenshot`: Takes a screenshot of the Godot editor to help you visualize the current state of the project.
  - The game directory is the current directory (`./`)
  - This is useful for understanding the scene hierarchy, node structure, and visual layout
  - You can use this before making changes to understand the current state, and after to verify your changes

When to use the MCP tools:
- Before starting work: Use `godot-screenshot` to understand the current project structure
- After making changes: Use `godot-screenshot` to verify your changes are correct
- When debugging: Use `godot-screenshot` to see what the editor looks like and identify issues
"""
        instruction += mcp_guidance

    return instruction


def create_system_prompt(use_mcp: bool = False) -> str:
    """Create system prompt for Godot game development tasks.

    Args:
        use_mcp: Deprecated - MCP guidance is now in create_task_prompt

    Returns:
        System prompt string
    """
    return "You are a Godot game development expert."
