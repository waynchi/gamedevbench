#!/usr/bin/env python3

import os
from pathlib import Path

# Directory paths
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
TASKS_DIR = PROJECT_ROOT / "tasks"
GT_TASKS_DIR = PROJECT_ROOT / "tasks_gt"
RESULTS_FOLDER = PROJECT_ROOT / "results"

# Godot configuration
GODOT_EXEC_PATH = os.environ.get("GODOT_EXEC_PATH", "godot")
SUPPORTED_GODOT_VERSION = "4.4.1"
GODOT_PROJECT_NAME = "project.godot"
TEST_SCENE_NAME = "res://scenes/test.tscn"

# Execution settings
TIMEOUT = 600
