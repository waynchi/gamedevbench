#!/usr/bin/env python3
"""Fail-closed solver filesystem and network confinement."""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Sequence, Tuple
from urllib.parse import urlsplit

from gamedevbench.src.provider_proxy import ProviderProxy, normalize_host
from gamedevbench.src.utils.data_types import SolverResult, TokenUsage


CONFINEMENT_PROFILE = "strict-v1"
PROXY_PORT = 3128


class ConfinementError(RuntimeError):
    """Raised when strict confinement cannot be established or maintained."""


DEFAULT_PROVIDER_HOSTS = {
    # Keep defaults on API endpoints rather than broad provider domains.  The
    # suffix matcher still permits an endpoint's own subdomains, while denying
    # documentation, search, storage, and other unrelated provider services.
    "muse": ("api.meta.ai",),
    "claude-code": ("api.anthropic.com",),
    "codex": ("api.openai.com", "chatgpt.com"),
    "gemini-cli": (
        "cloudcode-pa.googleapis.com",
        "generativelanguage.googleapis.com",
        "oauth2.googleapis.com",
    ),
    "mini-swe": ("api.anthropic.com", "api.openai.com"),
    "opencode": ("openrouter.ai",),
    "openhands": ("api.openai.com",),
}

# General solver web access remains fail-closed except for official Godot 4.4
# documentation. The proxy's suffix matching permits this host and any of its
# subdomains, while continuing to deny unrelated godotengine.org services.
DEFAULT_DOCUMENTATION_HOSTS = ("docs.godotengine.org",)


def _anthropic_gateway_host() -> Optional[str]:
    """Return the ANTHROPIC_BASE_URL host when a gateway is configured."""
    base_url = os.environ.get("ANTHROPIC_BASE_URL", "").strip()
    if not base_url:
        return None
    parts = urlsplit(base_url)
    if parts.scheme != "https" or not parts.hostname:
        raise ConfinementError(
            f"ANTHROPIC_BASE_URL must be an https:// URL, got {base_url!r}"
        )
    if parts.port not in (None, 443):
        raise ConfinementError(
            "ANTHROPIC_BASE_URL must use port 443; the provider proxy "
            "forwards only 443"
        )
    try:
        return normalize_host(parts.hostname)
    except ValueError as error:
        raise ConfinementError(
            f"Invalid ANTHROPIC_BASE_URL host {parts.hostname!r}: {error}"
        ) from error


def provider_hosts_for(
    agent: str,
    model: Optional[str],
    additional_hosts: Sequence[str] = (),
) -> Tuple[str, ...]:
    """Return provider and documentation hosts allowed for a solver selection."""
    hosts = set(DEFAULT_PROVIDER_HOSTS.get(agent, ()))
    hosts.update(DEFAULT_DOCUMENTATION_HOSTS)
    model_lower = (model or "").lower()

    if agent == "claude-code":
        # Gateway deployments point the Claude CLI at ANTHROPIC_BASE_URL
        # instead of api.anthropic.com; the gateway host is recorded in the
        # run's confinement metadata like every other allowed host.
        gateway_host = _anthropic_gateway_host()
        if gateway_host:
            hosts.add(gateway_host)

    if agent in {"opencode", "openhands"}:
        if "anthropic" in model_lower or "claude" in model_lower:
            hosts.add("api.anthropic.com")
        if "openrouter" in model_lower:
            hosts.add("openrouter.ai")
        if "google" in model_lower or "gemini" in model_lower:
            hosts.add("generativelanguage.googleapis.com")
        if "openai" in model_lower or model_lower.startswith("gpt"):
            hosts.add("api.openai.com")

    for host in additional_hosts:
        try:
            hosts.add(normalize_host(host.removeprefix("*.")))
        except ValueError as error:
            raise ConfinementError(
                f"Invalid additional provider host {host!r}: {error}"
            ) from error
    if not hosts:
        raise ConfinementError(
            f"No provider hosts are known for agent '{agent}'. "
            "Pass --provider-host explicitly."
        )
    return tuple(sorted(hosts))


def validate_confinement_available() -> str:
    """Validate strict Linux confinement and return the Bubblewrap version."""
    if platform.system() != "Linux":
        raise ConfinementError(
            "Strict solver confinement currently requires Linux. "
            "Use --confinement off only for explicitly untrusted/non-score runs."
        )
    binary = shutil.which("bwrap")
    if not binary:
        raise ConfinementError(
            "Strict solver confinement requires Bubblewrap (bwrap)."
        )
    try:
        completed = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ConfinementError(f"Bubblewrap preflight failed: {error}") from error
    return (completed.stdout or completed.stderr).strip()


def _copy_file_if_present(source: Path, destination: Path) -> None:
    if not source.is_file():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _copy_tree_if_present(source: Path, destination: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, destination, dirs_exist_ok=True)


def _stage_private_home(agent: str, destination: Path) -> None:
    """Copy only authentication and static provider data into a clean home."""
    source_home = Path.home()
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)

    if agent == "muse":
        _copy_file_if_present(
            source_home / ".config" / "muse" / "auth.json",
            destination / ".config" / "muse" / "auth.json",
        )
        muse_data = source_home / ".local" / "share" / "muse"
        for name in ("model-catalog", "feature-config", "skills", "plugins"):
            _copy_tree_if_present(
                muse_data / name,
                destination / ".local" / "share" / "muse" / name,
            )
    elif agent == "codex":
        _copy_file_if_present(
            source_home / ".codex" / "auth.json",
            destination / ".codex" / "auth.json",
        )
        # Custom model providers and their provider-scoped credentials live in
        # config.toml rather than auth.json (for example, GPT-sol gateways).
        _copy_file_if_present(
            source_home / ".codex" / "config.toml",
            destination / ".codex" / "config.toml",
        )
    elif agent == "claude-code":
        _copy_file_if_present(
            source_home / ".claude" / ".credentials.json",
            destination / ".claude" / ".credentials.json",
        )
    elif agent == "gemini-cli":
        for name in ("oauth_creds.json", "google_accounts.json"):
            _copy_file_if_present(
                source_home / ".gemini" / name,
                destination / ".gemini" / name,
            )
    elif agent == "opencode":
        _copy_file_if_present(
            source_home / ".local" / "share" / "opencode" / "auth.json",
            destination / ".local" / "share" / "opencode" / "auth.json",
        )

    # Gemini policy blocks provider-mediated web tools, which an IP firewall
    # alone cannot see.
    policy = destination / ".gemini" / "policies" / "gamedevbench.toml"
    policy.parent.mkdir(parents=True, exist_ok=True)
    policy.write_text(
        """[[rule]]
toolName = "google_web_search"
decision = "deny"
priority = 999

[[rule]]
toolName = "web_fetch"
decision = "deny"
priority = 999
""",
        encoding="utf-8",
    )


def _safe_environment() -> Dict[str, str]:
    """Construct a small environment without unrelated host credentials."""
    allowed_variables = (
        "OR_SITE_URL",
        "OR_APP_NAME",
        "OPENROUTER_API_BASE",
    )
    environment = {
        key: os.environ[key]
        for key in allowed_variables
        if os.environ.get(key)
    }
    environment.update(
        {
            "HOME": "/home/sandbox",
            "USER": "sandbox",
            "LOGNAME": "sandbox",
            "XDG_CONFIG_HOME": "/home/sandbox/.config",
            "XDG_DATA_HOME": "/home/sandbox/.local/share",
            "XDG_CACHE_HOME": "/home/sandbox/.cache",
            "XDG_STATE_HOME": "/home/sandbox/.local/state",
            "XDG_RUNTIME_DIR": "/tmp/runtime-sandbox",
            "TMPDIR": "/tmp",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "TERM": "dumb",
            "GAMEDEVBENCH_CONFINED": "1",
            "GAMEDEVBENCH_TOOL_NETWORK": "none",
            "MUSE_UPDATE_INTERVAL_SECONDS": "2147483647",
            "HTTP_PROXY": f"http://127.0.0.1:{PROXY_PORT}",
            "HTTPS_PROXY": f"http://127.0.0.1:{PROXY_PORT}",
            "ALL_PROXY": f"http://127.0.0.1:{PROXY_PORT}",
            "http_proxy": f"http://127.0.0.1:{PROXY_PORT}",
            "https_proxy": f"http://127.0.0.1:{PROXY_PORT}",
            "all_proxy": f"http://127.0.0.1:{PROXY_PORT}",
            "NO_PROXY": "",
            "no_proxy": "",
        }
    )
    return environment


def _secret_environment(agent: str, model: Optional[str]) -> Dict[str, str]:
    """Collect only credentials needed by the selected solver/provider."""
    model_lower = (model or "").lower()
    keys: Tuple[str, ...]
    if agent == "claude-code":
        # Gateway deployments authenticate with ANTHROPIC_AUTH_TOKEN against
        # ANTHROPIC_BASE_URL rather than an api.anthropic.com key.
        keys = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL")
    elif agent == "codex":
        config_path = Path.home() / ".codex" / "config.toml"
        config_text = (
            config_path.read_text(encoding="utf-8")
            if config_path.is_file()
            else ""
        )
        provider_match = re.search(
            r'^model_provider\s*=\s*["\']([^"\']+)["\']',
            config_text,
            flags=re.MULTILINE,
        )
        provider = provider_match.group(1).lower() if provider_match else "openai"
        keys = ("OPENAI_API_KEY",) if provider == "openai" else ()
    elif agent == "gemini-cli":
        keys = ("GEMINI_API_KEY", "GOOGLE_API_KEY")
    elif agent == "mini-swe":
        keys = (
            ("OPENAI_API_KEY",)
            if model_lower in {"gpt", "openai"} or model_lower.startswith("gpt")
            else ("ANTHROPIC_API_KEY",)
        )
    elif agent in {"opencode", "openhands"}:
        if "anthropic" in model_lower or "claude" in model_lower:
            keys = ("ANTHROPIC_API_KEY",)
        elif "google" in model_lower or "gemini" in model_lower:
            keys = ("GEMINI_API_KEY", "GOOGLE_API_KEY")
        elif "openrouter" in model_lower:
            keys = ("OPENROUTER_API_KEY",)
        elif "openai" in model_lower or model_lower.startswith("gpt"):
            keys = ("OPENAI_API_KEY",)
        else:
            keys = ()
    else:
        keys = ()
    return {
        key: os.environ[key]
        for key in keys
        if os.environ.get(key)
    }


def _add_bind(command: list, option: str, source: Path, destination: str) -> None:
    if source.exists():
        command.extend([option, str(source), destination])


def _add_filtered_etc_mounts(command: list) -> None:
    """Expose runtime data, not the host's complete system configuration."""
    for name in (
        "alternatives",
        "ca-certificates",
        "fonts",
        "group",
        "ld.so.cache",
        "ld.so.conf",
        "ld.so.conf.d",
        "localtime",
        "nsswitch.conf",
        "passwd",
        "pki",
        "ssl",
        "timezone",
        "vulkan",
    ):
        _add_bind(command, "--ro-bind", Path("/etc") / name, f"/etc/{name}")


def build_bwrap_command(
    *,
    agent: str,
    workspace: Path,
    private_home: Path,
    output_dir: Path,
    proxy_dir: Path,
    worker_config: Path,
    worker_output: Path,
    use_private_display: bool,
    godot_path: str,
    inner_command: Optional[Sequence[str]] = None,
) -> list:
    """Build the strict Bubblewrap command for one solver worker."""
    binary = shutil.which("bwrap")
    if not binary:
        raise ConfinementError("Bubblewrap (bwrap) is unavailable")

    project_root = Path(__file__).resolve().parents[2]
    package_root = project_root / "gamedevbench"
    virtualenv = project_root / ".venv"
    local_bin = Path.home() / ".local" / "bin"
    resolved_godot = (
        shutil.which(godot_path) if not os.path.isabs(godot_path) else godot_path
    )
    if not resolved_godot:
        raise ConfinementError(
            f"Configured Godot executable is unavailable: {godot_path}"
        )
    resolved_godot_path = Path(resolved_godot).resolve()
    if not resolved_godot_path.is_file():
        raise ConfinementError(
            f"Configured Godot executable is unavailable: {godot_path}"
        )

    command = [
        binary,
        "--unshare-all",
        "--die-with-parent",
        "--new-session",
        "--cap-drop",
        "ALL",
        "--hostname",
        "gamedevbench-solver",
        "--clearenv",
        "--ro-bind",
        "/usr",
        "/usr",
        "--ro-bind-try",
        "/bin",
        "/bin",
        "--ro-bind-try",
        "/lib",
        "/lib",
        "--ro-bind-try",
        "/lib64",
        "/lib64",
        "--ro-bind-try",
        "/sbin",
        "/sbin",
        "--tmpfs",
        "/etc",
        "--ro-bind-try",
        "/sys",
        "/sys",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
        "--dir",
        "/tmp/runtime-sandbox",
        "--chmod",
        "0700",
        "/tmp/runtime-sandbox",
        "--tmpfs",
        "/run",
        "--tmpfs",
        "/home",
        "--dir",
        "/home/sandbox",
        "--bind",
        str(private_home),
        "/home/sandbox",
        "--bind",
        str(workspace),
        "/workspace",
        "--bind",
        str(output_dir),
        "/run/gamedevbench-output",
        "--dir",
        "/run/gamedevbench-bin",
        "--ro-bind",
        str(resolved_godot_path),
        "/run/gamedevbench-bin/godot",
        "--ro-bind",
        str(proxy_dir),
        "/run/gamedevbench-proxy",
        "--dir",
        str(project_root.parent.parent),
        "--dir",
        str(project_root.parent),
        "--dir",
        str(project_root),
    ]

    _add_filtered_etc_mounts(command)

    _add_bind(command, "--ro-bind", package_root, str(package_root))
    _add_bind(command, "--ro-bind", virtualenv, str(virtualenv))
    command.extend(
        [
            "--dir",
            str(local_bin.parent.parent),
            "--dir",
            str(local_bin.parent),
            "--dir",
            str(local_bin),
        ]
    )
    # Expose only required user-installed launchers, never the whole user bin
    # directory (which may contain unrelated tools or alternate engines).
    tool_names = {"uv", "uvx"}
    if agent == "muse":
        tool_names.update(
            path.name
            for pattern in ("muse", "muse-bin-*", ".muse-*")
            for path in local_bin.glob(pattern)
        )
    elif agent == "claude-code":
        tool_names.add("claude")
    elif agent == "opencode":
        tool_names.add("opencode")
    for name in sorted(tool_names):
        source = local_bin / name
        _add_bind(command, "--ro-bind", source, str(source))

    # Claude's launcher in ~/.local/bin is a symlink into this version store.
    claude_store = Path.home() / ".local" / "share" / "claude"
    command.extend(["--dir", str(claude_store.parent)])
    _add_bind(command, "--ro-bind", claude_store, str(claude_store))

    # OpenCode reads these project-level configurations by absolute path.
    for name in ("opencode.json", "opencode.mcp.json"):
        source = project_root / name
        if source.is_file():
            command.extend(["--ro-bind", str(source), str(source)])

    environment = _safe_environment()
    environment["PATH"] = ":".join(
        (
            "/run/gamedevbench-bin",
            str(virtualenv / "bin"),
            str(local_bin),
            "/usr/local/bin",
            "/usr/bin",
            "/bin",
        )
    )
    environment["GODOT_EXEC_PATH"] = "/run/gamedevbench-bin/godot"
    muse_auth = private_home / ".config" / "muse" / "auth.json"
    if muse_auth.is_file():
        environment["MUSE_AUTH_PATH"] = "/home/sandbox/.config/muse/auth.json"
    for key, value in environment.items():
        command.extend(["--setenv", key, value])

    worker = list(inner_command) if inner_command is not None else [
        sys.executable,
        "-m",
        "gamedevbench.src.solver_worker",
        "--config",
        f"/run/gamedevbench-output/{worker_config.name}",
        "--output",
        f"/run/gamedevbench-output/{worker_output.name}",
    ]
    if use_private_display:
        xvfb_run = Path("/usr/bin/xvfb-run")
        if not xvfb_run.exists():
            raise ConfinementError(
                "Runtime video/MCP confinement requires xvfb-run"
            )
        worker = [
            str(xvfb_run),
            "-a",
            "-s",
            "-screen 0 1280x720x24",
            *worker,
        ]

    command.extend(
        [
            "--chdir",
            "/workspace",
            sys.executable,
            "-m",
            "gamedevbench.src.provider_proxy",
            "--socket",
            "/run/gamedevbench-proxy/provider.sock",
            "--port",
            str(PROXY_PORT),
            "--",
            *worker,
        ]
    )
    return command


def build_validation_bwrap_command(
    *,
    workspace: Path,
    godot_path: str,
    godot_args: Sequence[str],
    use_private_display: bool,
) -> list:
    """Build a credential-free, networkless Godot validation command."""
    binary = shutil.which("bwrap")
    if not binary:
        raise ConfinementError("Bubblewrap (bwrap) is unavailable")

    resolved_godot = (
        shutil.which(godot_path) if not os.path.isabs(godot_path) else godot_path
    )
    if not resolved_godot:
        raise ConfinementError(
            f"Configured Godot executable is unavailable: {godot_path}"
        )
    resolved_godot_path = Path(resolved_godot).resolve()
    if not resolved_godot_path.is_file():
        raise ConfinementError(
            f"Configured Godot executable is unavailable: {godot_path}"
        )

    command = [
        binary,
        "--unshare-all",
        "--die-with-parent",
        "--new-session",
        "--cap-drop",
        "ALL",
        "--hostname",
        "gamedevbench-validator",
        "--clearenv",
        "--ro-bind",
        "/usr",
        "/usr",
        "--ro-bind-try",
        "/bin",
        "/bin",
        "--ro-bind-try",
        "/lib",
        "/lib",
        "--ro-bind-try",
        "/lib64",
        "/lib64",
        "--ro-bind-try",
        "/sbin",
        "/sbin",
        "--tmpfs",
        "/etc",
        "--ro-bind-try",
        "/sys",
        "/sys",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
        "--dir",
        "/tmp/runtime-sandbox",
        "--chmod",
        "0700",
        "/tmp/runtime-sandbox",
        "--tmpfs",
        "/run",
        "--tmpfs",
        "/home",
        "--dir",
        "/home/sandbox",
        "--bind",
        str(workspace),
        "/workspace",
        "--dir",
        "/run/gamedevbench-bin",
        "--ro-bind",
        str(resolved_godot_path),
        "/run/gamedevbench-bin/godot",
    ]
    _add_filtered_etc_mounts(command)

    environment = {
        "HOME": "/home/sandbox",
        "USER": "sandbox",
        "LOGNAME": "sandbox",
        "XDG_CONFIG_HOME": "/home/sandbox/.config",
        "XDG_DATA_HOME": "/home/sandbox/.local/share",
        "XDG_CACHE_HOME": "/home/sandbox/.cache",
        "XDG_STATE_HOME": "/home/sandbox/.local/state",
        "XDG_RUNTIME_DIR": "/tmp/runtime-sandbox",
        "TMPDIR": "/tmp",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TERM": "dumb",
        "PATH": "/run/gamedevbench-bin:/usr/local/bin:/usr/bin:/bin",
        "GODOT_EXEC_PATH": "/run/gamedevbench-bin/godot",
        "GAMEDEVBENCH_CONFINED": "1",
        "GAMEDEVBENCH_TOOL_NETWORK": "none",
    }
    for key, value in environment.items():
        command.extend(["--setenv", key, value])

    inner = ["/run/gamedevbench-bin/godot", *godot_args]
    if use_private_display:
        xvfb_run = Path("/usr/bin/xvfb-run")
        if not all(
            path.exists()
            for path in (xvfb_run, Path("/usr/bin/Xvfb"), Path("/usr/bin/xauth"))
        ):
            raise ConfinementError(
                "Display validation confinement requires xvfb-run, Xvfb, and xauth"
            )
        inner = [
            str(xvfb_run),
            "-a",
            "-s",
            "-screen 0 1280x720x24",
            *inner,
        ]
    command.extend(["--chdir", "/workspace", *inner])
    return command


def run_confined_godot(
    *,
    workspace: Path,
    godot_path: str,
    godot_args: Sequence[str],
    use_private_display: bool,
    timeout_seconds: int,
) -> subprocess.CompletedProcess:
    """Run Godot with no credentials, host filesystem, or network route."""
    validate_confinement_available()
    command = build_validation_bwrap_command(
        workspace=workspace,
        godot_path=godot_path,
        godot_args=godot_args,
        use_private_display=use_private_display,
    )
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )


def _solver_result_from_dict(data: dict) -> SolverResult:
    usage_data = data.get("token_usage")
    usage = TokenUsage(**usage_data) if isinstance(usage_data, dict) else None
    return SolverResult(
        success=bool(data.get("success", False)),
        message=str(data.get("message", "")),
        duration_seconds=float(data.get("duration_seconds", 0.0)),
        stdout=str(data.get("stdout", "")),
        stderr=str(data.get("stderr", "")),
        timestamp=str(data.get("timestamp", "")),
        is_rate_limited=bool(data.get("is_rate_limited", False)),
        token_usage=usage,
        model=str(data.get("model", "")),
        cost_usd=float(data.get("cost_usd", 0.0)),
    )


@dataclass
class ConfinedSolverRun:
    result: SolverResult
    metadata: dict


def run_confined_solver(
    *,
    workspace: Path,
    agent: str,
    model: Optional[str],
    debug: bool,
    use_mcp: bool,
    timeout_seconds: Optional[int],
    use_runtime_video: bool,
    effort: Optional[str],
    godot_path: str,
    additional_provider_hosts: Sequence[str] = (),
) -> ConfinedSolverRun:
    """Run one solver with a private filesystem and provider-only egress."""
    bwrap_version = validate_confinement_available()
    provider_hosts = provider_hosts_for(agent, model, additional_provider_hosts)

    with (
        tempfile.TemporaryDirectory(prefix="gdb-home-") as home_root,
        tempfile.TemporaryDirectory(prefix="gdb-output-") as output_root,
        tempfile.TemporaryDirectory(prefix="gdb-proxy-") as proxy_root,
    ):
        private_home = Path(home_root)
        output_dir = Path(output_root)
        proxy_dir = Path(proxy_root)
        _stage_private_home(agent, private_home)

        config = {
            "agent": agent,
            "model": model,
            "debug": debug,
            "use_mcp": use_mcp,
            "timeout_seconds": timeout_seconds,
            "use_runtime_video": use_runtime_video,
            "effort": effort,
        }
        worker_config = output_dir / "config.json"
        worker_output = output_dir / "result.json"
        secret_environment = _secret_environment(agent, model)
        if secret_environment:
            secret_environment_path = output_dir / "secret-environment.json"
            secret_environment_path.write_text(
                json.dumps(secret_environment), encoding="utf-8"
            )
            secret_environment_path.chmod(0o600)
            config["secret_environment_file"] = (
                "/run/gamedevbench-output/secret-environment.json"
            )
        worker_config.write_text(json.dumps(config), encoding="utf-8")

        proxy_socket = proxy_dir / "provider.sock"
        command = build_bwrap_command(
            agent=agent,
            workspace=workspace,
            private_home=private_home,
            output_dir=output_dir,
            proxy_dir=proxy_dir,
            worker_config=worker_config,
            worker_output=worker_output,
            use_private_display=use_runtime_video or use_mcp,
            godot_path=godot_path,
        )
        outer_timeout = timeout_seconds + 120 if timeout_seconds else None
        with ProviderProxy(proxy_socket, provider_hosts) as proxy:
            try:
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=outer_timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired as error:
                raise ConfinementError(
                    f"Confined solver exceeded outer timeout ({outer_timeout}s)"
                ) from error

        if debug and completed.stderr:
            print(completed.stderr, file=sys.stderr, end="")
        if not worker_output.is_file():
            diagnostic = (completed.stderr or completed.stdout or "").strip()
            raise ConfinementError(
                "Confined solver did not produce a result"
                + (f": {diagnostic[-2000:]}" if diagnostic else "")
            )
        try:
            result_data = json.loads(worker_output.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ConfinementError(
                f"Invalid confined solver result: {error}"
            ) from error

        metadata = {
            "profile": CONFINEMENT_PROFILE,
            "filesystem": "bubblewrap-private-mount-namespace",
            "network": "isolated-provider-proxy",
            "provider_hosts": list(provider_hosts),
            "bubblewrap_version": bwrap_version,
            "private_tmp": True,
            "private_home": True,
            "private_display": bool(use_runtime_video or use_mcp),
            **proxy.audit.to_dict(),
        }
        return ConfinedSolverRun(
            result=_solver_result_from_dict(result_data),
            metadata=metadata,
        )
