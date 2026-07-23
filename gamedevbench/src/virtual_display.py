"""Virtual display support for Linux benchmark runs."""

import os
import shutil
import sys
from typing import Mapping, Optional, Sequence


VIRTUAL_DISPLAY_ENV = "GAMEDEVBENCH_XVFB"


class VirtualDisplayError(RuntimeError):
    """Raised when a Linux run needs a display but Xvfb is unavailable."""


def needs_virtual_display(
    command: Optional[str],
    skip_display: bool,
    *,
    environ: Optional[Mapping[str, str]] = None,
    platform: Optional[str] = None,
) -> bool:
    """Return whether this CLI invocation should be re-executed under Xvfb."""
    current_environ = os.environ if environ is None else environ
    current_platform = sys.platform if platform is None else platform

    if not current_platform.startswith("linux") or current_environ.get("DISPLAY"):
        return False
    return command in {"run", "validate"} and not skip_display


def ensure_virtual_display(
    command: Optional[str],
    skip_display: bool,
    *,
    argv: Optional[Sequence[str]] = None,
) -> None:
    """Re-execute a display-capable Linux command under ``xvfb-run``."""
    if not needs_virtual_display(command, skip_display):
        return

    xvfb_run = shutil.which("xvfb-run")
    if not xvfb_run or not shutil.which("Xvfb") or not shutil.which("xauth"):
        raise VirtualDisplayError(
            "A display is required, but Xvfb is unavailable. Install the "
            "'xvfb' and 'xauth' OS packages or set DISPLAY."
        )

    original_args = list(sys.argv[1:] if argv is None else argv)
    exec_args = [
        xvfb_run,
        "-a",
        sys.executable,
        "-m",
        "gamedevbench.src.benchmark_runner",
        *original_args,
    ]
    child_environ = os.environ.copy()
    child_environ[VIRTUAL_DISPLAY_ENV] = "1"
    print("DISPLAY is unset; starting benchmark under Xvfb.", flush=True)
    os.execvpe(xvfb_run, exec_args, child_environ)
