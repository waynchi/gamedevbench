#!/usr/bin/env python3

import re
import json
import os
from typing import Dict, List, Optional, Tuple
from pathlib import Path

from gamedevbench.src.utils.constants import PROJECT_ROOT
from gamedevbench.src.utils.data_types import ValidationResult


class ValidationParser:
    """Parser for Godot validation script outputs."""

    VALIDATION_PASSED_PATTERN = r"VALIDATION_PASSED(?::\s*(.+))?"
    VALIDATION_FAILED_PATTERN = r"VALIDATION_FAILED(?::\s*(.+))?"

    @staticmethod
    def parse_output(output: str, debug: bool = False) -> ValidationResult:
        """
        Parse validation output from Godot script.

        Args:
            output: Raw output from Godot validation script
            debug: If True, preserve full output in details

        Returns:
            ValidationResult object with parsed results
        """
        lines = output.strip().split("\n")

        # Look for validation markers
        for line in lines:
            line = line.strip()

            # Check for success
            passed_match = re.search(ValidationParser.VALIDATION_PASSED_PATTERN, line)
            if passed_match:
                message = passed_match.group(1) or "Validation passed"
                details = {"full_output": output} if debug else {}
                return ValidationResult(True, message, details)

            # Check for failure
            failed_match = re.search(ValidationParser.VALIDATION_FAILED_PATTERN, line)
            if failed_match:
                message = failed_match.group(1) or "Validation failed"
                details = {"full_output": output} if debug else {}
                return ValidationResult(False, message, details)

        # If no validation markers found, treat as failure
        details = {"output": output}
        return ValidationResult(
            False, "No validation result found in output", details
        )
    
    @staticmethod
    def save_result_to_json(task_name: str, result: ValidationResult, results_dir: Path = None):
        """
        Save validation result to JSON file.
        
        Args:
            task_name: Name of the task that was validated
            result: ValidationResult object to save
            results_dir: Directory to save results (defaults to project root/results)
        """
        if results_dir is None:
            # Default to results directory in project root
            results_dir = PROJECT_ROOT / "results"
        
        # Create results directory if it doesn't exist
        results_dir.mkdir(exist_ok=True)
        
        # Create filename based on task name
        filename = f"task_{task_name}.json"
        filepath = results_dir / filename
        
        # Save result to JSON
        with open(filepath, 'w') as f:
            json.dump(result.to_dict(), f, indent=2)
        
        print(f"Validation result saved to: {filepath}")
