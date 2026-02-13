#!/usr/bin/env python3

import subprocess
import json
import argparse
import os
import shutil
import csv
import yaml
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from gamedevbench.src.utils.constants import (
    TASKS_DIR,
    GT_TASKS_DIR,
    GODOT_EXEC_PATH,
    GODOT_PROJECT_NAME,
    TEST_SCENE_NAME,
    RESULTS_FOLDER,
    TIMEOUT,
)
from gamedevbench.src.utils.data_types import ValidationResult
from gamedevbench.src.utils.validation import ValidationParser
from gamedevbench.src.solver_factory import SolverFactory


class GodotBenchmarkRunner:
    def __init__(
        self,
        use_gt: bool,
        agent: Optional[str] = None,
        model: str = "claude",
        debug: bool = False,
        resume: bool = False,
        use_mcp: bool = False,
        resume_from: Optional[str] = None,
        skip_display: bool = False,
        use_runtime_video: bool = False,
    ):
        """
        Initialize the benchmark runner.

        Args:
            use_gt: Whether to use ground truth tasks directory
            agent: Agent to use for solving tasks (see SolverFactory.get_available_agents() for options)
            model: Model to use (agent-specific; see SolverFactory for details)
            debug: Whether to show debug output
            resume: Whether to resume from previous progress
            use_mcp: Enable MCP server functionality when supported by the agent
            resume_from: Path to a results JSON file to resume from (skips solver_success=true, redoes solver_success=false)
            skip_display: Skip tasks that require display (requires_display=true in task_config.json)
            use_runtime_video: Enable runtime video mode (appends Godot runtime instructions to prompts)
        """
        self.godot_path = GODOT_EXEC_PATH
        if use_gt:
            self.tasks_dir = GT_TASKS_DIR
        else:
            self.tasks_dir = TASKS_DIR
        self.agent = agent
        self.model = model
        self.debug = debug
        self.resume = resume
        # Sanitize model for filesystem (litellm models often include '/')
        safe_model = model.replace("/", "_") if model else "default"
        self.progress_file = RESULTS_FOLDER / f"progress_{agent}_{safe_model}.json"
        self.use_mcp = use_mcp
        self.resume_from = resume_from
        self.skip_display = skip_display
        self.use_runtime_video = use_runtime_video

        # Validate agent configuration early if agent is specified
        if self.agent:
            self._validate_agent_configuration()

    def _validate_agent_configuration(self):
        """
        Validate agent configuration early to provide helpful error messages.

        Raises:
            ValueError: If agent is unknown or configuration is invalid
        """
        # Check if agent exists
        available_agents = SolverFactory.get_available_agents()
        if self.agent not in available_agents:
            raise ValueError(
                f"Unknown agent: {self.agent}. "
                f"Available agents: {', '.join(available_agents)}"
            )

        # Get solver capabilities
        solver_info = SolverFactory.get_solver_info(self.agent)

        # Warn if MCP is requested but not supported
        if self.use_mcp and not solver_info["supports_mcp"]:
            mcp_capable = SolverFactory.get_mcp_capable_solvers()
            raise ValueError(
                f"Agent '{self.agent}' does not support MCP. "
                f"Set use_mcp=False or use a solver that supports MCP. "
                f"MCP-capable solvers: {', '.join(mcp_capable)}"
            )

        # Provide informational message in debug mode
        if self.debug:
            print(f"Agent: {self.agent}")
            print(f"  - MCP Support: {solver_info['supports_mcp']}")
            print(f"  - System Prompt Support: {solver_info['supports_system_prompt']}")
            if self.use_mcp:
                print(f"  - MCP Enabled: Yes")

    def list_tasks(self) -> List[str]:
        """List all available benchmark tasks."""
        if not self.tasks_dir.exists():
            return []

        tasks = []
        for task_dir in self.tasks_dir.iterdir():
            if task_dir.is_dir() and (task_dir / GODOT_PROJECT_NAME).exists():
                tasks.append(task_dir.name)

        return sorted(tasks)

    def _save_progress(self, completed_tasks: List[str], results: List[Dict]):
        """Save progress to a JSON file."""
        RESULTS_FOLDER.mkdir(exist_ok=True)
        progress_data = {
            "completed_tasks": completed_tasks,
            "results": results,
            "timestamp": datetime.now().isoformat(),
        }
        with open(self.progress_file, "w") as f:
            json.dump(progress_data, f, indent=2)
        if self.debug:
            print(f"Progress saved to: {self.progress_file}")

    def _load_progress(self) -> Tuple[List[str], List[Dict]]:
        """Load progress from a JSON file."""
        if not self.progress_file.exists():
            return [], []

        try:
            with open(self.progress_file, "r") as f:
                progress_data = json.load(f)
            completed_tasks = progress_data.get("completed_tasks", [])
            results = progress_data.get("results", [])
            if self.debug:
                print(
                    f"Loaded progress: {len(completed_tasks)} tasks already completed"
                )
            return completed_tasks, results
        except Exception as e:
            print(f"Error loading progress file: {e}")
            return [], []

    def _load_results_from_file(self, results_file: str) -> Tuple[List[str], List[str], List[Dict]]:
        """
        Load results from a JSON file and determine which tasks to skip/redo.

        Args:
            results_file: Path to the results JSON file

        Returns:
            Tuple of (tasks_to_skip, tasks_to_redo, previous_results)
            - tasks_to_skip: Tasks with solver_success=true
            - tasks_to_redo: Tasks with solver_success=false
            - previous_results: All results from tasks_to_skip (to be included in final results)
        """
        results_path = Path(results_file)
        if not results_path.exists():
            print(f"Results file not found: {results_file}")
            return [], [], []

        try:
            with open(results_path, "r") as f:
                data = json.load(f)

            tasks_to_skip = []
            tasks_to_redo = []
            previous_results = []

            # Extract tasks from the results
            tasks_data = data.get("tasks", [])
            for task_result in tasks_data:
                task_name = task_result.get("task_name")
                solver_success = task_result.get("solver_success", False)

                if task_name:
                    if solver_success:
                        tasks_to_skip.append(task_name)
                        previous_results.append(task_result)
                    else:
                        tasks_to_redo.append(task_name)

            if self.debug:
                print(f"Loaded results from: {results_file}")
                print(f"  Tasks to skip (solver_success=true): {len(tasks_to_skip)}")
                print(f"  Tasks to redo (solver_success=false): {len(tasks_to_redo)}")

            return tasks_to_skip, tasks_to_redo, previous_results

        except Exception as e:
            print(f"Error loading results file: {e}")
            return [], [], []

    def _clear_progress(self):
        """Clear the progress file."""
        if self.progress_file.exists():
            self.progress_file.unlink()
            if self.debug:
                print(f"Progress file cleared: {self.progress_file}")

    def load_task_config(self, task_name: str) -> Optional[Dict]:
        """Load task configuration from task_config.json."""
        config_path = self.tasks_dir / task_name / "task_config.json"

        try:
            with open(config_path, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading config for task {task_name}: {e}")
            return None

    def open_task(self, task_name: str) -> bool:
        """
        Open a task in Godot editor for the CUA to work on.

        Args:
            task_name: Name of the task to open

        Returns:
            True if task opened successfully, False otherwise
        """
        task_dir = self.tasks_dir / task_name
        project_file = task_dir / GODOT_PROJECT_NAME

        if not project_file.exists():
            print(f"Task '{task_name}' not found at {project_file}")
            return False

        # TODO turn config into a dataclass for cleanliness
        config = self.load_task_config(task_name)
        if config:
            print(f"Task: {config.get('name', task_name)}")
            print(f"Description: {config.get('description', 'No description')}")
            print(f"Instructions: {config.get('instructions', 'No instructions')}")
            print("-" * 50)

        try:
            # Open Godot editor with the specific project
            cmd = [self.godot_path, "--editor", "--path", str(task_dir)]
            print(f"Opening task '{task_name}' in Godot...")
            print(f"Command: {' '.join(cmd)}")

            # Launch Godot and don't wait for it to close
            subprocess.Popen(cmd)
            print(f"Godot editor opened for task: {task_name}")
            return True

        except Exception as e:
            print(f"Error opening task: {e}")
            return False

    def create_validation_scene(self, task_dir: Path) -> bool:
        """Create a validation scene that copies main.tscn and adds test node."""
        try:
            # Read the original main.tscn
            main_scene_path = task_dir / "scenes" / "main.tscn"
            if not main_scene_path.exists():
                print(f"Main scene not found: {main_scene_path}")
                return False

            with open(main_scene_path, "r") as f:
                main_content = f.read()

            # Create validation scene content by adding test node
            validation_content = main_content.rstrip()
            validation_content += """

[node name="ValidationTest" type="Node" parent="."]
script = ExtResource("test_script")
"""

            # Add the test script as an external resource
            # Find the load_steps number and increment it
            import re

            load_steps_match = re.search(r"load_steps=(\d+)", validation_content)
            if load_steps_match:
                current_steps = int(load_steps_match.group(1))
                new_steps = current_steps + 1
                validation_content = validation_content.replace(
                    f"load_steps={current_steps}", f"load_steps={new_steps}"
                )

            # Add the test script resource
            # Find where external resources end and add our script
            ext_resource_pattern = r"(\[ext_resource[^\]]+\]\n)"
            resources = re.findall(ext_resource_pattern, validation_content)
            if resources:
                last_resource = resources[-1]
                # Get the next ID number
                id_pattern = r'id="(\d+)_[^"]*"'
                ids = re.findall(id_pattern, validation_content)
                next_id = max([int(id_match) for id_match in ids]) + 1 if ids else 1

                new_resource = f'[ext_resource type="Script" path="res://scripts/test.gd" id="{next_id}_test"]\n'
                validation_content = validation_content.replace(
                    last_resource, last_resource + new_resource
                )

                # Update the script reference
                validation_content = validation_content.replace(
                    'script = ExtResource("test_script")',
                    f'script = ExtResource("{next_id}_test")',
                )

            # Write the validation scene
            validation_scene_path = task_dir / "scenes" / "validation_scene.tscn"
            with open(validation_scene_path, "w") as f:
                f.write(validation_content)

            return True

        except Exception as e:
            print(f"Error creating validation scene: {e}")
            return False

    def validate_task(self, task_name: str) -> ValidationResult:
        """
        Run validation tests for a task.

        Args:
            task_name: Name of the task to validate

        Returns:
            ValidationResult object with test results
        """

        task_dir = self.tasks_dir / task_name
        project_file = task_dir / "project.godot"

        if not project_file.exists():
            result = ValidationResult(False, f"Task '{task_name}' not found")
            ValidationParser.save_result_to_json(task_name, result, RESULTS_FOLDER)
            return result

        # Check if task requires display
        config = self.load_task_config(task_name)
        requires_display = config.get("requires_display", False) if config else False

        try:
            # Determine if we should use headless mode
            use_headless = not requires_display

            # Run editor to ensure project files are loaded
            cmd = [
                self.godot_path,
                "--editor",
                "--log-file",
                "-",
                "--path",
                str(task_dir),
            ]
            if use_headless:
                cmd.insert(1, "--headless")

            try:
                subprocess_result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=3
                )
                # TODO Move this to a script that sets up the entire repo
                print("Loading editor to ensure project files are fully loaded")
            except subprocess.TimeoutExpired:
                print("Loaded")

            # Run test scene
            cmd = [
                self.godot_path,
                "--log-file",
                "-",
                "--path",
                str(task_dir),
                TEST_SCENE_NAME,
            ]
            if use_headless:
                cmd.insert(1, "--headless")

            mode_str = "headless mode" if use_headless else "display mode"
            print(f"Running validation for task: {task_name} ({mode_str})")
            subprocess_result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=TIMEOUT
            )

            # Parse the output for test results
            output = subprocess_result.stdout + subprocess_result.stderr
            result = ValidationParser.parse_output(output, debug=self.debug)

            # Save result to JSON file
            ValidationParser.save_result_to_json(task_name, result, RESULTS_FOLDER)

            return result

        except subprocess.TimeoutExpired:
            result = ValidationResult(False, "Validation timed out")
            ValidationParser.save_result_to_json(task_name, result, RESULTS_FOLDER)
            return result
        except Exception as e:
            result = ValidationResult(False, f"Error running validation: {e}")
            ValidationParser.save_result_to_json(task_name, result, RESULTS_FOLDER)
            return result

    def run_benchmark(self, task_name: str) -> Dict:
        """
        Run a complete benchmark cycle.
        If agent is specified, solve with the agent first, then validate.
        Otherwise, just validate the current state.
        """
        # Check if task should be skipped due to display requirement
        if self.skip_display:
            config = self.load_task_config(task_name)
            requires_display = config.get("requires_display", False) if config else False
            if requires_display:
                return {
                    "task_name": task_name,
                    "success": False,
                    "skipped": True,
                    "message": "Task skipped: requires display (requires_display=true)",
                    "timestamp": datetime.now().isoformat(),
                    "agent": self.agent,
                    "model": self.model,
                    "use_mcp": self.use_mcp,
                    "use_runtime_video": self.use_runtime_video,
                    "skip_display": self.skip_display,
                    "debug": self.debug,
                }

        if self.agent:
            return self._run_benchmark_with_agent(task_name)
        else:
            return self._run_benchmark_validate_only(task_name)

    def _run_benchmark_validate_only(self, task_name: str) -> Dict:
        """Run benchmark with validation only (original behavior)."""
        result = self.validate_task(task_name)
        return {
            "task_name": task_name,
            "success": result.success,
            "message": result.message,
            "timestamp": result.timestamp,
            "agent": self.agent,
            "model": self.model,
            "use_mcp": self.use_mcp,
            "use_runtime_video": self.use_runtime_video,
            "skip_display": self.skip_display,
            "debug": self.debug,
        }

    def _create_sandbox_environment(self, task_dir: Path) -> Path:
        """
        Create an isolated sandbox environment for the agent to work in.

        This prevents agents from:
        1. Reading test files (test.gd, test.tscn)
        2. Reading task_config.json (contains answers/hints)
        3. Running validation commands
        4. Accessing the original task directory

        Args:
            task_dir: Original task directory

        Returns:
            Path to the sandbox directory in /tmp
        """
        # Create unique sandbox directory in /tmp
        sandbox_id = f"gamedevbench_sandbox_{uuid.uuid4().hex[:8]}"
        sandbox_dir = Path(tempfile.gettempdir()) / sandbox_id
        sandbox_dir.mkdir(parents=True, exist_ok=True)

        if self.debug:
            print(f"      Creating sandbox at: {sandbox_dir}")

        # Blacklist approach: Copy everything EXCEPT explicitly excluded items
        # DO NOT copy:
        # - test.gd, test.tscn, and related files (test.gd.uid, etc.)
        # - task_config.json (we create a minimal version instead)
        # - *.log files
        # - *.md files (markdown documentation)
        # - Hidden files (starting with '.')
        # - .backup folders

        def should_skip_file(file_path: Path) -> bool:
            """Check if a file should be skipped during copy."""
            name = file_path.name.lower()

            # Skip test-related files
            if name.startswith("test"):
                return True

            # Skip task_config.json (we'll create minimal version)
            if name == "task_config.json":
                return True

            # Skip log files
            if name.endswith(".log"):
                return True

            # Skip markdown files
            if name.endswith(".md"):
                return True

            # Skip hidden files
            if name.startswith("."):
                return True

            return False

        def should_skip_directory(dir_path: Path) -> bool:
            """Check if a directory should be skipped during copy."""
            name = dir_path.name.lower()

            # Skip hidden directories
            if name.startswith("."):
                return True

            # Skip backup directories
            if name == ".backup":
                return True

            return False

        def copy_directory_filtered(src_dir: Path, dst_dir: Path):
            """Recursively copy directory with filtering."""
            dst_dir.mkdir(parents=True, exist_ok=True)

            for item in src_dir.iterdir():
                if item.is_file():
                    if not should_skip_file(item):
                        shutil.copy2(item, dst_dir / item.name)
                elif item.is_dir():
                    if not should_skip_directory(item):
                        copy_directory_filtered(item, dst_dir / item.name)

        # Copy all items from task_dir to sandbox_dir with filtering
        for item in task_dir.iterdir():
            if item.is_file():
                if not should_skip_file(item):
                    shutil.copy2(item, sandbox_dir / item.name)
            elif item.is_dir():
                if not should_skip_directory(item):
                    copy_directory_filtered(item, sandbox_dir / item.name)

        # Create a minimal task_config.json with only the instruction
        # This gives the agent the task without any hints or test information
        task_config_src = task_dir / "task_config.json"
        if task_config_src.exists():
            try:
                with open(task_config_src, "r") as f:
                    full_config = json.load(f)
                # Only include instruction field - nothing else that could help cheat
                minimal_config = {
                    "instruction": full_config.get(
                        "instruction", "No instruction provided"
                    )
                }
                with open(sandbox_dir / "task_config.json", "w") as f:
                    json.dump(minimal_config, f, indent=2)
            except Exception as e:
                if self.debug:
                    print(f"      Warning: Could not create minimal task_config: {e}")

        return sandbox_dir

    def _copy_sandbox_results_to_validation(
        self, sandbox_dir: Path, validation_dir: Path, task_dir: Path
    ):
        """
        Copy agent's work from sandbox to validation directory and add test files.

        Args:
            sandbox_dir: The sandbox where agent worked
            validation_dir: Where validation will run
            task_dir: Original task directory (for test files)
        """
        # Copy everything from sandbox to validation
        for item in sandbox_dir.iterdir():
            dst = validation_dir / item.name
            if item.is_dir():
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(item, dst)
            else:
                shutil.copy2(item, dst)

        # Now add test files from original task directory
        # Copy test.gd
        test_gd_src = task_dir / "scripts" / "test.gd"
        if test_gd_src.exists():
            scripts_dst = validation_dir / "scripts"
            scripts_dst.mkdir(parents=True, exist_ok=True)
            shutil.copy2(test_gd_src, scripts_dst / "test.gd")
            # Also copy test.gd.uid if it exists
            test_gd_uid = task_dir / "scripts" / "test.gd.uid"
            if test_gd_uid.exists():
                shutil.copy2(test_gd_uid, scripts_dst / "test.gd.uid")

        # Copy test.tscn
        test_tscn_src = task_dir / "scenes" / "test.tscn"
        if test_tscn_src.exists():
            scenes_dst = validation_dir / "scenes"
            scenes_dst.mkdir(parents=True, exist_ok=True)
            shutil.copy2(test_tscn_src, scenes_dst / "test.tscn")

    def _validate_in_directory(
        self, validation_dir: Path, task_name: str
    ) -> "ValidationResult":
        """
        Run validation in a specific directory.

        Args:
            validation_dir: Directory containing the project to validate
            task_name: Name of the task (for logging)

        Returns:
            ValidationResult object
        """
        project_file = validation_dir / "project.godot"

        if not project_file.exists():
            return ValidationResult(
                False, f"project.godot not found in validation directory"
            )

        # Check if task requires display
        config = self.load_task_config(task_name)
        requires_display = config.get("requires_display", False) if config else False
        use_headless = not requires_display

        try:
            # Run editor to ensure project files are loaded
            cmd = [
                self.godot_path,
                "--editor",
                "--log-file",
                "-",
                "--path",
                str(validation_dir),
            ]
            if use_headless:
                cmd.insert(1, "--headless")

            try:
                subprocess.run(cmd, capture_output=True, text=True, timeout=3)
                if self.debug:
                    print(
                        "      Loading editor to ensure project files are fully loaded"
                    )
            except subprocess.TimeoutExpired:
                if self.debug:
                    print("      Loaded")

            # Run test scene
            cmd = [
                self.godot_path,
                "--log-file",
                "-",
                "--path",
                str(validation_dir),
                TEST_SCENE_NAME,
            ]
            if use_headless:
                cmd.insert(1, "--headless")

            if self.debug:
                mode_str = "headless mode" if use_headless else "display mode"
                print(f"      Running validation in: {validation_dir} ({mode_str})")

            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=TIMEOUT
            )
            output = result.stdout + result.stderr
            validation_result = ValidationParser.parse_output(output, debug=self.debug)

            # Save to results folder
            ValidationParser.save_result_to_json(
                task_name, validation_result, RESULTS_FOLDER
            )

            return validation_result

        except subprocess.TimeoutExpired:
            return ValidationResult(False, "Validation timed out")
        except Exception as e:
            return ValidationResult(False, f"Error running validation: {e}")

    def _save_test_result(
        self, task_dir: Path, task_name: str, validation_result=None, solver_result=None
    ):
        """Save current task state and validation result to test_result folder in parent directory."""
        # Save to tasks/test_result/ instead of tasks/task_xxxx/test_result/
        result_dir = self.tasks_dir / "test_result"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        result_subdir = result_dir / f"{task_name}_{self.agent}_{timestamp}"

        result_subdir.mkdir(parents=True, exist_ok=True)

        # Copy all files except backup and hidden test file
        for item in task_dir.iterdir():
            if item.name not in [".backup", ".test.gd.hidden", "agent_trajectory.log"]:
                src = item
                dst = result_subdir / item.name
                if item.is_dir():
                    shutil.copytree(src, dst)
                else:
                    shutil.copy2(src, dst)

        # Copy log file if exists
        log_file = task_dir / "agent_trajectory.log"
        if log_file.exists():
            shutil.copy2(log_file, result_subdir / "agent_trajectory.log")

        # Save validation result to result.json
        if validation_result or solver_result:
            result_json = {
                "task_name": task_name,
                "agent": self.agent,
                "model": self.model,
                "timestamp": timestamp,
            }

            # Add validation result
            if validation_result:
                result_json["validation"] = {
                    "success": validation_result.success,
                    "message": validation_result.message,
                    "timestamp": validation_result.timestamp,
                }

            # Add solver result with token usage
            if solver_result:
                solver_data = {
                    "success": solver_result.success,
                    "message": solver_result.message,
                    "duration_seconds": solver_result.duration_seconds,
                    "is_rate_limited": solver_result.is_rate_limited,
                    "model": solver_result.model,
                    "cost_usd": solver_result.cost_usd,
                }
                # Add token usage if available
                if solver_result.token_usage:
                    solver_data["token_usage"] = solver_result.token_usage.to_dict()
                result_json["solver"] = solver_data

            # Write to result.json
            result_json_path = result_subdir / "result.json"
            with open(result_json_path, "w") as f:
                json.dump(result_json, f, indent=2)

        return result_subdir

    def _run_benchmark_with_agent(self, task_name: str) -> Dict:
        """
        Run benchmark with agent solving in an isolated sandbox, then validation.

        The agent works in a clean /tmp directory that contains only:
        - assets/ folder
        - scenes/ folder (without test.tscn)
        - scripts/ folder (without test.gd)
        - *.tres resource files
        - project.godot

        This prevents the agent from:
        - Reading test files to cheat
        - Accessing task_config.json
        - Running validation commands
        """
        task_dir = self.tasks_dir / task_name

        if not task_dir.exists():
            return {
                "task_name": task_name,
                "success": False,
                "message": f"Task directory not found: {task_dir}",
                "timestamp": datetime.now().isoformat(),
                "agent": self.agent,
                "model": self.model,
                "use_mcp": self.use_mcp,
                "use_runtime_video": self.use_runtime_video,
                "skip_display": self.skip_display,
                "debug": self.debug,
            }

        # Determine actual model name for logging
        display_model = self.model
        sandbox_dir = None
        validation_dir = None

        try:
            # Step 1: Create isolated sandbox environment in /tmp
            if self.debug:
                print(f"[1/5] Creating isolated sandbox environment...")
            sandbox_dir = self._create_sandbox_environment(task_dir)

            # Step 1.5: Load sandbox in headless editor to ensure sprites/assets are imported
            if self.debug:
                print(f"[1.5/5] Loading sandbox in editor to import assets...")
            try:
                cmd = [
                    self.godot_path,
                    "--headless",
                    "--editor",
                    "--log-file",
                    "-",
                    "--path",
                    str(sandbox_dir),
                ]
                subprocess.run(cmd, capture_output=True, text=True, timeout=3)
                if self.debug:
                    print("      Assets loaded and imported")
            except subprocess.TimeoutExpired:
                if self.debug:
                    print("      Assets loaded and imported")

            # Step 2: Run agent in sandbox
            original_cwd = os.getcwd()
            solver_result = None
            log_file_path = sandbox_dir / "agent_trajectory.log"

            try:
                os.chdir(sandbox_dir)
                if self.debug:
                    print(
                        f"[2/5] Solving task with {self.agent} (model: {display_model}): {task_name}"
                    )
                    print(f"      Working directory: {sandbox_dir}")

                # Create solver using factory pattern
                solver = SolverFactory.create_solver(
                    agent=self.agent,
                    debug=self.debug,
                    model=self.model,
                    use_mcp=self.use_mcp,
                    timeout_seconds=TIMEOUT,
                    use_runtime_video=self.use_runtime_video,
                )
                solver_result = solver.solve_task()

                # Save solver output to log file
                with open(log_file_path, "w") as f:
                    f.write(f"Task: {task_name}\n")
                    f.write(f"Agent: {self.agent}\n")
                    f.write(f"Model: {display_model}\n")
                    f.write(f"Sandbox: {sandbox_dir}\n")
                    f.write(f"Timestamp: {datetime.now().isoformat()}\n")
                    f.write("=" * 80 + "\n\n")
                    if solver_result:
                        f.write(f"Success: {solver_result.success}\n")
                        f.write(f"Message: {solver_result.message}\n")
                        f.write(f"Duration: {solver_result.duration_seconds:.2f}s\n\n")
                        f.write("STDOUT:\n")
                        f.write(solver_result.stdout or "")
                        f.write("\n\nSTDERR:\n")
                        f.write(solver_result.stderr or "")

                if self.debug:
                    print(
                        f"      Solver completed in {solver_result.duration_seconds:.2f}s"
                    )

            except Exception as e:
                if self.debug:
                    print(f"Error during {self.agent} solving: {e}")
                from gamedevbench.src.utils.data_types import SolverResult

                solver_result = SolverResult(
                    success=False,
                    message=f"Error during solving: {str(e)}",
                    duration_seconds=0.0,
                )
            finally:
                os.chdir(original_cwd)

            # Step 3: Create validation directory with agent's work + test files
            if self.debug:
                print(f"[3/5] Preparing validation environment...")
            validation_id = f"gamedevbench_validation_{uuid.uuid4().hex[:8]}"
            validation_dir = Path(tempfile.gettempdir()) / validation_id
            validation_dir.mkdir(parents=True, exist_ok=True)

            # Copy agent's work and add test files
            self._copy_sandbox_results_to_validation(
                sandbox_dir, validation_dir, task_dir
            )

            # Step 4: Run validation
            if self.debug:
                print(f"[4/5] Running validation...")
            validation_result = self._validate_in_directory(validation_dir, task_name)

            # Step 5: Save results
            if self.debug:
                print(f"[5/5] Saving results...")
            result_subdir = self._save_test_result(
                validation_dir, task_name, validation_result, solver_result
            )

            # Copy log file to result directory
            if log_file_path.exists():
                shutil.copy2(log_file_path, result_subdir / "agent_trajectory.log")

            if self.debug:
                print(f"✓ Benchmark cycle completed for {task_name}")
                print(
                    f"  Results saved to: {result_subdir.relative_to(self.tasks_dir.parent)}"
                )

            # Extract token usage info
            token_usage = None
            input_tokens = 0
            output_tokens = 0
            total_tokens = 0
            cost_usd = 0.0
            if solver_result and solver_result.token_usage:
                token_usage = solver_result.token_usage
                input_tokens = token_usage.input_tokens
                output_tokens = token_usage.output_tokens
                total_tokens = token_usage.total_tokens
                cost_usd = solver_result.cost_usd

            return {
                "task_name": task_name,
                "success": validation_result.success,
                "message": validation_result.message,
                "timestamp": validation_result.timestamp,
                "agent": self.agent,
                "model": display_model,
                "use_mcp": self.use_mcp,
                "use_runtime_video": self.use_runtime_video,
                "skip_display": self.skip_display,
                "debug": self.debug,
                "solver_success": solver_result.success if solver_result else False,
                "solver_message": (
                    solver_result.message if solver_result else "No solver result"
                ),
                "solver_duration": (
                    solver_result.duration_seconds if solver_result else 0.0
                ),
                "is_rate_limited": (
                    solver_result.is_rate_limited if solver_result else False
                ),
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
                "cost_usd": cost_usd,
                "sandbox_dir": str(sandbox_dir) if sandbox_dir else "",
                "result_dir": str(result_subdir.relative_to(self.tasks_dir.parent)),
            }

        finally:
            # Clean up temporary directories
            if sandbox_dir and sandbox_dir.exists():
                try:
                    shutil.rmtree(sandbox_dir)
                    if self.debug:
                        print(f"  Cleaned up sandbox: {sandbox_dir}")
                except Exception as e:
                    if self.debug:
                        print(f"  Warning: Could not clean up sandbox: {e}")

            if validation_dir and validation_dir.exists():
                try:
                    shutil.rmtree(validation_dir)
                    if self.debug:
                        print(f"  Cleaned up validation dir: {validation_dir}")
                except Exception as e:
                    if self.debug:
                        print(f"  Warning: Could not clean up validation dir: {e}")

    def _save_final_results(
        self,
        success_count: int,
        failure_count: int,
        error_count: int,
        skipped_count: int,
        results: List[Dict],
        rate_limited: bool = False
    ):
        """Save final results to JSON and CSV files after each task completion."""
        total_tasks = len(results)
        final_results = self._create_final_results_summary(
            success_count, failure_count, error_count, skipped_count, total_tasks, results
        )

        # Add rate limit info to final results
        final_results["rate_limited"] = rate_limited
        if rate_limited:
            final_results["incomplete"] = True
            final_results["remaining_tasks"] = len(self.list_tasks()) - total_tasks

        # Save final results to JSON
        RESULTS_FOLDER.mkdir(exist_ok=True)
        final_results_path = RESULTS_FOLDER / "final_results.json"
        with open(final_results_path, "w") as f:
            json.dump(final_results, f, indent=2)

        # Also save to agent-model-specific file if agent is specified
        if self.agent:
            safe_model = self.model.replace("/", "_") if self.model else "default"
            agent_model_results_path = RESULTS_FOLDER / f"{self.agent}_{safe_model}_final_results.json"
            with open(agent_model_results_path, "w") as f:
                json.dump(final_results, f, indent=2)

        # Save results to CSV
        csv_path = RESULTS_FOLDER / "final_results.csv"
        self._save_results_to_csv(results, csv_path)

    def load_tasks_from_file(self, file_path: str) -> List[str]:
        """
        Load task names from a YAML file.

        Args:
            file_path: Path to the YAML file containing task names

        Returns:
            List of task names
        """
        path = Path(file_path)
        if path.suffix.lower() not in {".yaml", ".yml"}:
            print(
                f"Error: task list must be a YAML file (*.yaml or *.yml): {file_path}"
            )
            return []

        try:
            data = yaml.safe_load(path.read_text()) or {}
            if isinstance(data, dict):
                tasks = data.get("tasks") or data.get("task_list") or []
            elif isinstance(data, list):
                tasks = data
            else:
                tasks = []
            return [
                str(task).strip()
                for task in tasks
                if isinstance(task, str) and task.strip()
            ]
        except Exception as e:
            print(f"Error reading task list file: {e}")
            return []

    def run_all_tasks(self, task_list_file: Optional[str] = None) -> Dict:
        """
        Run validation on all available tasks and generate final results summary.

        Args:
            task_list_file: Optional path to a file containing task names to run

        Returns:
            Dictionary containing aggregated results
        """
        if task_list_file:
            tasks = self.load_tasks_from_file(task_list_file)
            if not tasks:
                print(f"No tasks found in file: {task_list_file}")
                return self._create_final_results_summary(0, 0, 0, 0, 0, [])
        else:
            tasks = self.list_tasks()
            if not tasks:
                print("No tasks found to run")
                return self._create_final_results_summary(0, 0, 0, 0, 0, [])

        # Load progress if resuming
        completed_tasks = []
        results = []

        # Check if resuming from a results file
        if self.resume_from:
            tasks_to_skip, tasks_to_redo, previous_results = self._load_results_from_file(self.resume_from)

            # Find new tasks that aren't in the results file
            all_processed_tasks = set(tasks_to_skip + tasks_to_redo)
            new_tasks = [t for t in tasks if t not in all_processed_tasks]

            print(f"Resuming from results file: {self.resume_from}")
            print(f"  Skipping {len(tasks_to_skip)} tasks with solver_success=true")
            print(f"  Re-running {len(tasks_to_redo)} tasks with solver_success=false")
            print(f"  Running {len(new_tasks)} new tasks not in results file")

            # Start with previous successful results
            results = previous_results
            completed_tasks = tasks_to_skip

            # Filter tasks to include:
            # 1. Tasks that need to be redone (solver_success=false)
            # 2. Tasks that are not in the results file at all (new tasks)
            # Exclude tasks with solver_success=true
            tasks = [t for t in tasks if t not in tasks_to_skip]

            if not tasks:
                print("No tasks to run!")
                # Return existing results
                success_count = sum(1 for r in results if r.get("success", False))
                skipped_count_existing = sum(1 for r in results if r.get("skipped", False))
                failure_count = len(results) - success_count - skipped_count_existing
                return self._create_final_results_summary(
                    success_count, failure_count, 0, skipped_count_existing, len(results), results
                )
        elif self.resume:
            completed_tasks, results = self._load_progress()
            if completed_tasks:
                print(
                    f"Resuming from previous run. {len(completed_tasks)} tasks already completed."
                )
                # Filter out already completed tasks
                tasks = [t for t in tasks if t not in completed_tasks]
                if not tasks:
                    print("All tasks already completed!")
                    # Return existing results
                    success_count = sum(1 for r in results if r.get("success", False))
                    skipped_count_existing = sum(1 for r in results if r.get("skipped", False))
                    failure_count = len(results) - success_count - skipped_count_existing
                    return self._create_final_results_summary(
                        success_count, failure_count, 0, skipped_count_existing, len(results), results
                    )

        success_count = sum(1 for r in results if r.get("success", False))
        failure_count = len(results) - success_count
        error_count = 0
        skipped_count = 0
        rate_limited = False

        print(f"Running validation on {len(tasks)} tasks...")

        for task_name in tasks:
            try:
                print(f"Running benchmark for task: {task_name}")
                task_result = self.run_benchmark(task_name)
                results.append(task_result)
                completed_tasks.append(task_name)

                if task_result.get("skipped", False):
                    skipped_count += 1
                    print(f"  → Skipped: {task_result.get('message', 'Unknown reason')}")
                elif task_result["success"]:
                    success_count += 1
                else:
                    failure_count += 1

                # Check if this was a rate limit error
                if task_result.get("solver_result") and hasattr(
                    task_result["solver_result"], "is_rate_limited"
                ):
                    rate_limited = task_result["solver_result"].is_rate_limited
                elif "is_rate_limited" in task_result:
                    rate_limited = task_result["is_rate_limited"]

                # Save progress after each task
                self._save_progress(completed_tasks, results)

                # Save final results after each task (for early termination visibility)
                self._save_final_results(success_count, failure_count, error_count, skipped_count, results, rate_limited)

                # Exit early if rate limited
                if rate_limited:
                    print("\n" + "=" * 80)
                    print("⚠️  API RATE LIMIT/QUOTA EXCEEDED - STOPPING EXECUTION")
                    print("=" * 80)
                    print(f"Completed {len(completed_tasks)} tasks before rate limit.")
                    print(f"Progress saved to: {self.progress_file}")
                    print(f"Use --resume flag to continue from where you left off.")
                    print("=" * 80 + "\n")
                    break

            except Exception as e:
                error_count += 1
                error_result = ValidationResult(False, f"Error running task: {e}")
                task_result = {
                    "task_name": task_name,
                    "success": error_result.success,
                    "message": error_result.message,
                    "timestamp": error_result.timestamp,
                    "agent": self.agent,
                    "model": self.model,
                    "use_mcp": self.use_mcp,
                    "use_runtime_video": self.use_runtime_video,
                    "skip_display": self.skip_display,
                    "debug": self.debug,
                }
                results.append(task_result)
                completed_tasks.append(task_name)
                print(f"Error running task {task_name}: {e}")

                # Save progress even on error
                self._save_progress(completed_tasks, results)

                # Save final results after error
                self._save_final_results(success_count, failure_count, error_count, skipped_count, results, rate_limited)

        total_tasks = len(results)
        final_results = self._create_final_results_summary(
            success_count, failure_count, error_count, skipped_count, total_tasks, results
        )

        # Add rate limit info to final results
        final_results["rate_limited"] = rate_limited
        if rate_limited:
            final_results["incomplete"] = True
            final_results["remaining_tasks"] = len(self.list_tasks()) - len(
                completed_tasks
            )

        # Save final results to JSON
        RESULTS_FOLDER.mkdir(exist_ok=True)
        final_results_path = RESULTS_FOLDER / "final_results.json"
        with open(final_results_path, "w") as f:
            json.dump(final_results, f, indent=2)

        # Also save to agent-model-specific file if agent is specified
        if self.agent:
            safe_model = self.model.replace("/", "_") if self.model else "default"
            agent_model_results_path = RESULTS_FOLDER / f"{self.agent}_{safe_model}_final_results.json"
            with open(agent_model_results_path, "w") as f:
                json.dump(final_results, f, indent=2)

        # Save results to CSV
        csv_path = RESULTS_FOLDER / "final_results.csv"
        self._save_results_to_csv(results, csv_path)

        print(f"Final results saved to: {final_results_path}")
        print(f"CSV results saved to: {csv_path}")
        success_rate = final_results["task_success_rate"]
        status_msg = f"Summary: {success_count} passed, {failure_count} failed, {error_count} errors"
        if skipped_count > 0:
            status_msg += f", {skipped_count} skipped"
        status_msg += f" out of {total_tasks} total tasks ({success_rate}% success rate on {final_results['tasks_attempted']} attempted)"
        if rate_limited:
            status_msg += f" [INCOMPLETE - Rate limited, {final_results['remaining_tasks']} tasks remaining]"
        print(status_msg)

        # Print token/cost statistics
        token_stats = final_results.get("token_statistics", {})
        cost_stats = final_results.get("cost_statistics", {})
        duration_stats = final_results.get("duration_statistics", {})
        if token_stats.get("total_tokens", 0) > 0:
            print(f"\nToken Usage:")
            print(
                f"  Total: {token_stats['total_tokens']:,} tokens (input: {token_stats['total_input_tokens']:,}, output: {token_stats['total_output_tokens']:,})"
            )
            print(
                f"  Average per task: {token_stats['avg_tokens_per_task']:,.0f} tokens"
            )
            print(f"\nCost:")
            print(f"  Total: ${cost_stats['total_cost_usd']:.4f}")
            print(f"  Average per task: ${cost_stats['avg_cost_per_task_usd']:.4f}")
            print(f"\nDuration:")
            print(f"  Total: {duration_stats['total_duration_seconds']:.2f}s")
            print(
                f"  Average per task: {duration_stats['avg_duration_per_task_seconds']:.2f}s"
            )

        # Clear progress file only if completed without rate limit
        if not rate_limited and not final_results.get("incomplete", False):
            self._clear_progress()

        return final_results

    def _create_final_results_summary(
        self,
        success_count: int,
        failure_count: int,
        error_count: int,
        skipped_count: int,
        total_tasks: int,
        results: List[Dict],
    ) -> Dict:
        """Create standardized final results summary structure with token/cost statistics."""
        # Calculate success rate excluding skipped tasks
        tasks_attempted = total_tasks - skipped_count
        success_rate = (success_count / tasks_attempted) * 100 if tasks_attempted > 0 else 0.0

        # Calculate total token usage and cost
        total_input_tokens = sum(r.get("input_tokens", 0) for r in results)
        total_output_tokens = sum(r.get("output_tokens", 0) for r in results)
        total_tokens = sum(r.get("total_tokens", 0) for r in results)
        total_cost_usd = sum(r.get("cost_usd", 0.0) for r in results)
        total_duration = sum(r.get("solver_duration", 0.0) for r in results)

        # Calculate averages - only include tasks that actually ran (have non-zero token data)
        # This excludes skipped tasks and tasks that errored before running the solver
        tasks_with_data = sum(1 for r in results if r.get("total_tokens", 0) > 0)
        avg_input_tokens = total_input_tokens / tasks_with_data if tasks_with_data > 0 else 0
        avg_output_tokens = total_output_tokens / tasks_with_data if tasks_with_data > 0 else 0
        avg_tokens = total_tokens / tasks_with_data if tasks_with_data > 0 else 0
        avg_cost_usd = total_cost_usd / tasks_with_data if tasks_with_data > 0 else 0.0
        avg_duration = total_duration / tasks_with_data if tasks_with_data > 0 else 0.0

        return {
            "success": success_count,
            "failures": failure_count,
            "errors": error_count,
            "skipped": skipped_count,
            "total_tasks_ran": total_tasks,
            "tasks_attempted": tasks_attempted,
            "task_success_rate": round(success_rate, 2),
            "timestamp": datetime.now().isoformat(),
            # Configuration settings
            "configuration": {
                "agent": self.agent,
                "model": self.model,
                "use_mcp": self.use_mcp,
                "use_runtime_video": self.use_runtime_video,
                "skip_display": self.skip_display,
                "debug": self.debug,
            },
            # Token usage statistics
            "token_statistics": {
                "total_input_tokens": total_input_tokens,
                "total_output_tokens": total_output_tokens,
                "total_tokens": total_tokens,
                "avg_input_tokens": round(avg_input_tokens, 2),
                "avg_output_tokens": round(avg_output_tokens, 2),
                "avg_tokens_per_task": round(avg_tokens, 2),
            },
            # Cost statistics
            "cost_statistics": {
                "total_cost_usd": round(total_cost_usd, 4),
                "avg_cost_per_task_usd": round(avg_cost_usd, 4),
            },
            # Duration statistics
            "duration_statistics": {
                "total_duration_seconds": round(total_duration, 2),
                "avg_duration_per_task_seconds": round(avg_duration, 2),
            },
            "tasks": results,
        }

    def _save_results_to_csv(self, results: List[Dict], csv_path: Path):
        """Save results to CSV file with token usage and cost information."""
        if not results:
            return

        # Define CSV columns with token usage and cost
        fieldnames = [
            "task_name",
            "validation_success",
            "validation_message",
            "skipped",
            "agent",
            "model",
            "use_mcp",
            "use_runtime_video",
            "skip_display",
            "debug",
            "solver_success",
            "solver_message",
            "solver_duration_seconds",
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "cost_usd",
            "is_rate_limited",
            "timestamp",
            "log_file",
            "result_dir",
        ]

        with open(csv_path, "w", newline="") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()

            for result in results:
                row = {
                    "task_name": result.get("task_name", ""),
                    "validation_success": result.get("success", False),
                    "validation_message": result.get("message", ""),
                    "skipped": result.get("skipped", False),
                    "agent": result.get("agent", ""),
                    "model": result.get("model", ""),
                    "use_mcp": result.get("use_mcp", False),
                    "use_runtime_video": result.get("use_runtime_video", False),
                    "skip_display": result.get("skip_display", False),
                    "debug": result.get("debug", False),
                    "solver_success": result.get("solver_success", False),
                    "solver_message": result.get("solver_message", ""),
                    "solver_duration_seconds": result.get("solver_duration", 0.0),
                    "input_tokens": result.get("input_tokens", 0),
                    "output_tokens": result.get("output_tokens", 0),
                    "total_tokens": result.get("total_tokens", 0),
                    "cost_usd": result.get("cost_usd", 0.0),
                    "is_rate_limited": result.get("is_rate_limited", False),
                    "timestamp": result.get("timestamp", ""),
                    "log_file": result.get("log_file", ""),
                    "result_dir": result.get("result_dir", ""),
                }
                writer.writerow(row)


def main():
    parser = argparse.ArgumentParser(description="Godot Benchmark Runner")
    parser.add_argument("--gt", help="Use GT tasks directory", action="store_true")
    parser.add_argument(
        "--agent",
        choices=SolverFactory.get_available_agents(),
        help="Agent to use for solving tasks",
    )
    parser.add_argument(
        "--model",
        default="claude",
        help="Model to use (for claude-code: model name; for mini-swe: 'claude' or 'gpt'; for openhands: model name like 'gpt-4o'; for gemini-cli: model name like 'gemini-2.0-flash'; ignored for codex)",
    )
    parser.add_argument("--debug", help="Show debug output", action="store_true")
    parser.add_argument(
        "--resume",
        help="Resume from previous progress (only for run command with agent)",
        action="store_true",
    )
    parser.add_argument(
        "--resume-from",
        help="Resume from a specific results JSON file (skips tasks with solver_success=true, redoes tasks with solver_success=false)",
        type=str,
    )
    parser.add_argument(
        "--enable-mcp",
        help="Enable MCP server functionality for supported agents",
        action="store_true",
    )
    parser.add_argument(
        "--skip-display",
        help="Skip tasks that require display (requires_display=true in task_config.json)",
        action="store_true",
    )
    parser.add_argument(
        "--use-runtime-video",
        help="Enable runtime video mode (appends Godot runtime instructions to prompts)",
        action="store_true",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # List command
    subparsers.add_parser("list", help="List all available tasks")

    # Open command
    open_parser = subparsers.add_parser("open", help="Open a specific task in Godot")
    open_parser.add_argument("task_name", help="Name of the task to open")

    # Validate command
    validate_parser = subparsers.add_parser(
        "validate", help="Run validation tests for a task"
    )
    validate_parser.add_argument("task_name", help="Name of the task to validate")

    # Run command (single task)
    run_parser = subparsers.add_parser("run", help="Run complete benchmark cycle")
    run_parser.add_argument(
        "task_name",
        nargs="?",
        help="Name of the task to run (if not provided, runs all tasks)",
    )
    run_parser.add_argument(
        "--task-list",
        help="Path to a task list YAML file",
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    runner = GodotBenchmarkRunner(
        use_gt=args.gt,
        agent=args.agent,
        model=args.model,
        debug=args.debug,
        resume=args.resume,
        use_mcp=args.enable_mcp,
        resume_from=args.resume_from if hasattr(args, "resume_from") else None,
        skip_display=args.skip_display,
        use_runtime_video=args.use_runtime_video,
    )

    if args.command == "list":
        tasks = runner.list_tasks()
        if tasks:
            print("Available tasks:")
            for task in tasks:
                config = runner.load_task_config(task)
                name = config.get("name", task) if config else task
                print(f"  {task}: {name}")
        else:
            print("No tasks found")

    elif args.command == "open":
        runner.open_task(args.task_name)
    elif args.command == "validate":
        if runner.agent:
            # Use run_benchmark which includes agent solving
            result = runner.run_benchmark(args.task_name)
            print(f"Validation result: {'PASSED' if result['success'] else 'FAILED'}")
            print(f"Message: {result['message']}")
            if args.debug and "solver_message" in result:
                print(
                    f"Agent: {result.get('agent', 'unknown')}, Model: {result.get('model', 'unknown')}"
                )
                print(f"Solver result: {result['solver_message']}")
        else:
            # Direct validation only
            result = runner.validate_task(args.task_name)
            print(f"Validation result: {'PASSED' if result.success else 'FAILED'}")
            print(f"Message: {result.message}")
    elif args.command == "run":
        if args.task_name and hasattr(args, "task_list") and args.task_list:
            print("Error: Cannot specify both task_name and --task-list")
            return
        elif args.task_name:
            # Run single task
            result = runner.run_benchmark(args.task_name)
            print(json.dumps(result, indent=2))
        else:
            # Run all tasks or tasks from file
            task_list_file = args.task_list if hasattr(args, "task_list") else None
            result = runner.run_all_tasks(task_list_file=task_list_file)
            print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
