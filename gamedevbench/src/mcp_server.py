#!/usr/bin/env python3
"""
MCP Server for Godot screenshot functionality.
Launches Godot editor on specified screen and captures screenshots using AppleScript.
"""

import asyncio
import subprocess
import os
import sys
import base64
import tempfile
import json
import re
from pathlib import Path
from typing import Any, Dict
from PIL import Image
from io import BytesIO

from mcp.server import Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent, ImageContent

server = Server("godot-screenshot-server")

# Fixed wait time for Godot to load (in seconds)
GODOT_LOAD_WAIT_TIME = 8.0

# Default target display for screenshot (can be overridden via environment variable)
DEFAULT_TARGET_DISPLAY = int(os.environ.get('GODOT_SCREENSHOT_DISPLAY', '2'))

# Default resolution (1280x720 to keep file size smaller)
DEFAULT_RESOLUTION = "1280x720"

# JPEG quality for compression (0-100, lower = smaller file)
JPEG_QUALITY = 85

# Maximum target size in KB for base64 encoded image
MAX_TARGET_SIZE_KB = 150

@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools."""
    return [
        Tool(
            name="godot-screenshot",
            description="Launch Godot editor on specified display and take a screenshot",
            inputSchema={
                "type": "object",
                "properties": {
                    "project_dir": {
                        "type": "string",
                        "description": "Path to the Godot project directory"
                    },
                    "display": {
                        "type": "integer",
                        "description": "No need to specify this, it will be automatically determined by the MCP server. Do not specify this if you are not sure about the display number."
                    }
                },
                "required": ["project_dir"]
            }
        )
    ]

@server.call_tool()
async def call_tool(name: str, arguments: Dict[str, Any]) -> list[TextContent | ImageContent]:
    """Handle tool calls."""
    if name != "godot-screenshot":
        raise ValueError(f"Unknown tool: {name}")

    project_dir = arguments.get("project_dir")
    display = arguments.get("display", DEFAULT_TARGET_DISPLAY)

    if not project_dir:
        return [TextContent(type="text", text="Error: project_dir is required")]

    # Validate project directory exists
    if not os.path.isdir(project_dir):
        return [TextContent(type="text", text=f"Error: Project directory does not exist: {project_dir}")]

    # Check if it's a valid Godot project
    project_file = os.path.join(project_dir, "project.godot")
    if not os.path.exists(project_file):
        return [TextContent(type="text", text=f"Error: No project.godot file found in {project_dir}")]

    try:
        # Launch Godot editor and capture screenshot
        result = await launch_godot_and_screenshot(project_dir, display)

        if isinstance(result, str) and result.startswith("Error:"):
            return [TextContent(type="text", text=result)]

        screenshot_data, mime_type = result

        # Return the screenshot as base64 image content
        return [
            TextContent(type="text", text=f"Screenshot captured from Display {display} for project: {project_dir}"),
            ImageContent(
                type="image",
                data=screenshot_data,
                mimeType=mime_type
            )
        ]

    except Exception as e:
        return [TextContent(type="text", text=f"Error: {str(e)}")]

def compress_screenshot(image_bytes: bytes, target_size_kb: int = MAX_TARGET_SIZE_KB) -> tuple[bytes, str]:
    """Compress screenshot to target size.

    Args:
        image_bytes: Original PNG screenshot bytes
        target_size_kb: Target size in KB

    Returns:
        Tuple of (compressed_bytes, mime_type)
    """
    # Open the image
    img = Image.open(BytesIO(image_bytes))

    # Try different quality levels to get under target size
    quality = JPEG_QUALITY
    while quality > 10:
        output = BytesIO()
        img.convert('RGB').save(output, format='JPEG', quality=quality, optimize=True)
        compressed_bytes = output.getvalue()

        # Check base64 size (base64 is ~1.37x larger than raw bytes)
        estimated_b64_size = len(base64.b64encode(compressed_bytes))
        estimated_kb = estimated_b64_size / 1024

        print(f"Quality {quality}: {len(compressed_bytes)} bytes, ~{estimated_kb:.1f}KB base64")

        if estimated_kb <= target_size_kb:
            return compressed_bytes, "image/jpeg"

        quality -= 10

    # If still too large, resize the image
    print("Resizing image to reduce size further...")
    width, height = img.size
    img = img.resize((width // 2, height // 2), Image.Resampling.LANCZOS)

    output = BytesIO()
    img.convert('RGB').save(output, format='JPEG', quality=60, optimize=True)
    return output.getvalue(), "image/jpeg"

async def launch_godot_and_screenshot(project_dir: str, display: int = DEFAULT_TARGET_DISPLAY) -> tuple[str, str]:
    """Launch Godot editor on specified display and take a screenshot using AppleScript.

    Args:
        project_dir: Path to the Godot project directory
        display: Display number to use (Godot screen = display - 1)

    Returns:
        Tuple of (base64_screenshot_data, mime_type)
    """

    # Launch Godot editor on specific screen
    # Godot screen numbering: Display N = Screen N-1
    godot_screen = display - 1
    godot_cmd = [
        "godot",
        "--editor",
        "--windowed",
        "--path", project_dir,
        "--resolution", DEFAULT_RESOLUTION,
        "--screen", str(godot_screen),
        "--fullscreen"
    ]

    print(f"Launching Godot on screen {godot_screen} (Display {display})")

    try:
        # Start Godot process
        godot_process = subprocess.Popen(
            godot_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        # Wait for Godot to fully load and enter fullscreen
        print(f"Waiting {GODOT_LOAD_WAIT_TIME} seconds for Godot to load...")
        await asyncio.sleep(GODOT_LOAD_WAIT_TIME)

        # Create a temporary file for the screenshot
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp_file:
            screenshot_path = tmp_file.name

        # AppleScript to capture the specified display
        applescript = f'''
        do shell script "screencapture -D{display} '{screenshot_path}'"
        '''

        print(f"Taking screenshot of Display {display}")

        # Execute AppleScript
        result = subprocess.run(
            ['osascript', '-e', applescript],
            capture_output=True,
            text=True
        )

        if result.returncode != 0:
            error_msg = result.stderr
            print(f"Screenshot failed on Display {display}: {error_msg}")
            
            # Check if it's an invalid display error
            if "Invalid display" in error_msg and display != 1:
                print("Retrying with Display 1...")
                # Update Godot window to screen 0 (Display 1) if possible? 
                # Godot is already running on the other screen, but we just want to capture *a* screen.
                # Ideally we should restart Godot on screen 0, but that takes time. 
                # Let's just try to capture Display 1 (maybe Godot defaulted there if the other screen was invalid?)
                
                applescript_retry = f'''
                do shell script "screencapture -D1 '{screenshot_path}'"
                '''
                result_retry = subprocess.run(
                    ['osascript', '-e', applescript_retry],
                    capture_output=True,
                    text=True
                )
                
                if result_retry.returncode != 0:
                    # Clean up Godot process
                    if godot_process.poll() is None:
                        godot_process.terminate()
                        await asyncio.sleep(0.5)
                        if godot_process.poll() is None:
                            godot_process.kill()
                    return f"Error: Failed to capture screenshot (retry failed): {result_retry.stderr}"
            else:
                # Clean up Godot process
                if godot_process.poll() is None:
                    godot_process.terminate()
                    await asyncio.sleep(0.5)
                    if godot_process.poll() is None:
                        godot_process.kill()
                return f"Error: Failed to capture screenshot: {result.stderr}"

        # Read the screenshot file, compress it, and convert to base64
        try:
            with open(screenshot_path, 'rb') as f:
                screenshot_bytes = f.read()

            print(f"Original screenshot size: {len(screenshot_bytes)} bytes")

            # Compress the screenshot
            compressed_bytes, mime_type = compress_screenshot(screenshot_bytes)
            print(f"Compressed screenshot size: {len(compressed_bytes)} bytes")

            # Encode to base64
            screenshot_b64 = base64.b64encode(compressed_bytes).decode('utf-8')
            base64_size_kb = len(screenshot_b64) / 1024
            print(f"Base64 size: {base64_size_kb:.1f}KB")
        finally:
            # Clean up temporary file
            if os.path.exists(screenshot_path):
                os.remove(screenshot_path)

        # Kill Godot process after screenshot
        if godot_process.poll() is None:
            godot_process.terminate()
            await asyncio.sleep(0.5)
            if godot_process.poll() is None:
                godot_process.kill()

        return screenshot_b64, mime_type

    except FileNotFoundError:
        return "Error: Godot executable not found. Make sure Godot is installed and in your PATH."
    except Exception as e:
        # Clean up Godot process in case of error
        if 'godot_process' in locals() and godot_process.poll() is None:
            godot_process.terminate()
        return f"Error launching Godot or taking screenshot: {str(e)}"

async def run_server():
    """Run the MCP server."""
    # Server configuration
    init_options = InitializationOptions(
        server_name="godot-screenshot-server",
        server_version="1.0.0",
        capabilities={
            "tools": {}
        }
    )

    # Run server with stdio transport
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            init_options
        )

def main():
    """Entry point for the MCP server script."""
    asyncio.run(run_server())

if __name__ == "__main__":
    main()