import json
import os
import platform
import queue
import shutil
import socket
import ssl
import subprocess
from pathlib import Path

import pytest

from gamedevbench.src import benchmark_runner
from gamedevbench.src.benchmark_runner import GodotBenchmarkRunner
from gamedevbench.src.confinement import ConfinementError
from gamedevbench.src.confinement import _safe_environment
from gamedevbench.src.confinement import _secret_environment
from gamedevbench.src.confinement import build_bwrap_command
from gamedevbench.src.confinement import provider_hosts_for
from gamedevbench.src.confinement import run_confined_godot
from gamedevbench.src.provider_proxy import ProviderProxy, host_is_allowed
from gamedevbench.src.provider_proxy import NonPublicAddressError
from gamedevbench.src.provider_proxy import _connect_public_host
from gamedevbench.src.provider_proxy import _parse_client_hello_sni
from gamedevbench.src.utils.data_types import ValidationResult


def test_provider_suffix_matching_does_not_allow_lookalikes():
    assert host_is_allowed("api.meta.ai", ["meta.ai"])
    assert host_is_allowed("meta.ai", ["meta.ai"])
    assert not host_is_allowed("meta.ai.evil.example", ["meta.ai"])
    assert not host_is_allowed("notmeta.ai", ["meta.ai"])
    assert not host_is_allowed("127.0.0.1", ["meta.ai"])


def test_muse_default_egress_allows_api_and_godot_documentation():
    hosts = provider_hosts_for("muse", "muse-spark-1.2")
    assert hosts == ("api.meta.ai", "docs.godotengine.org")
    assert host_is_allowed("api.meta.ai", hosts)
    assert host_is_allowed("docs.godotengine.org", hosts)
    assert host_is_allowed("preview.docs.godotengine.org", hosts)
    assert not host_is_allowed("dev.meta.ai", hosts)
    assert not host_is_allowed("godotengine.org", hosts)
    assert not host_is_allowed("facebook.com", hosts)


def test_claude_code_gateway_base_url_is_allowlisted_and_forwarded(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://gateway.example.com/v1")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "gateway-token")
    hosts = provider_hosts_for("claude-code", None)
    assert "gateway.example.com" in hosts
    assert "api.anthropic.com" in hosts
    secrets = _secret_environment("claude-code", None)
    assert secrets["ANTHROPIC_AUTH_TOKEN"] == "gateway-token"
    assert secrets["ANTHROPIC_BASE_URL"] == "https://gateway.example.com/v1"
    # Other agents neither allowlist the gateway nor receive its token.
    assert "gateway.example.com" not in provider_hosts_for("muse", None)
    assert "ANTHROPIC_AUTH_TOKEN" not in _secret_environment("muse", None)


def test_claude_code_gateway_base_url_must_be_https_port_443(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://gateway.example.com")
    with pytest.raises(ConfinementError):
        provider_hosts_for("claude-code", None)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://gateway.example.com:8443")
    with pytest.raises(ConfinementError):
        provider_hosts_for("claude-code", None)


def test_secret_environment_is_not_placed_in_bubblewrap_arguments(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "canary-secret-must-not-enter-argv")
    assert "OPENAI_API_KEY" not in _safe_environment()
    assert "canary-secret-must-not-enter-argv" not in _safe_environment().values()


def test_custom_codex_provider_does_not_receive_unrelated_openai_key(
    monkeypatch, tmp_path
):
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(
        'model_provider = "arena"\n', encoding="utf-8"
    )
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "unrelated-openai-secret")
    assert "OPENAI_API_KEY" not in _secret_environment("codex", "gpt-5.6-sol")


def test_provider_proxy_rejects_non_allowlisted_connect(tmp_path):
    socket_path = tmp_path / "provider.sock"
    with ProviderProxy(socket_path, ["meta.ai"]) as proxy:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        with client:
            client.connect(str(socket_path))
            client.sendall(
                b"CONNECT raw.githubusercontent.com:443 HTTP/1.1\r\n\r\n"
            )
            response = client.recv(1024)

    assert response.startswith(b"HTTP/1.1 403")
    assert proxy.audit.denied == ["raw.githubusercontent.com:443"]
    assert proxy.audit.allowed == []


def test_provider_proxy_rejects_private_dns_resolution(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))
        ],
    )
    with pytest.raises(NonPublicAddressError):
        _connect_public_host("api.meta.ai", 443, 1.0)


def test_provider_proxy_clears_timeouts_before_relay(tmp_path, mocker):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    outgoing = ssl.MemoryBIO()
    tls = context.wrap_bio(
        ssl.MemoryBIO(),
        outgoing,
        server_side=False,
        server_hostname="api.meta.ai",
    )
    with pytest.raises(ssl.SSLWantReadError):
        tls.do_handshake()
    client_hello = outgoing.read()

    relay_timeouts = queue.Queue()
    mocker.patch(
        "gamedevbench.src.provider_proxy._relay_bidirectional",
        side_effect=lambda client, upstream: relay_timeouts.put(
            (client.gettimeout(), upstream.gettimeout())
        ),
    )

    upstream, provider = socket.socketpair()
    with upstream, provider:
        upstream.settimeout(15.0)
        provider.settimeout(2.0)
        connect = mocker.patch(
            "gamedevbench.src.provider_proxy._connect_public_host",
            return_value=upstream,
        )
        socket_path = tmp_path / "provider.sock"
        with ProviderProxy(socket_path, ["meta.ai"]) as proxy:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(2.0)
                client.connect(str(socket_path))
                client.sendall(b"CONNECT api.meta.ai:443 HTTP/1.1\r\n\r\n")
                response = bytearray()
                while b"\r\n\r\n" not in response:
                    chunk = client.recv(1024)
                    assert chunk, "proxy closed before responding to CONNECT"
                    response.extend(chunk)
                assert response.startswith(b"HTTP/1.1 200")
                client.sendall(client_hello)
                timeouts = relay_timeouts.get(timeout=2.0)
                with provider.makefile("rb") as received:
                    assert received.read(len(client_hello)) == client_hello

    connect.assert_called_once_with("api.meta.ai", 443, timeout=15.0)
    assert proxy.audit.allowed == ["api.meta.ai:443"]
    assert proxy.audit.denied == []
    assert timeouts == (None, None)


def test_tls_client_hello_sni_cannot_front_another_domain():
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    incoming = ssl.MemoryBIO()
    outgoing = ssl.MemoryBIO()
    tls = context.wrap_bio(
        incoming,
        outgoing,
        server_side=False,
        server_hostname="raw.githubusercontent.com",
    )
    with pytest.raises(ssl.SSLWantReadError):
        tls.do_handshake()

    assert _parse_client_hello_sni(outgoing.read()) == "raw.githubusercontent.com"


@pytest.mark.skipif(
    platform.system() != "Linux" or shutil.which("bwrap") is None,
    reason="Bubblewrap integration test requires Linux",
)
def test_bwrap_hides_ground_truth_siblings_and_public_network(tmp_path):
    workspace = tmp_path / "workspace"
    private_home = tmp_path / "home"
    output_dir = tmp_path / "output"
    proxy_dir = tmp_path / "proxy"
    for directory in (workspace, private_home, output_dir, proxy_dir):
        directory.mkdir()
    (workspace / "visible.txt").write_text("workspace-only", encoding="utf-8")
    forbidden_tmp = tmp_path / "forbidden.txt"
    forbidden_tmp.write_text("secret", encoding="utf-8")

    project_root = Path(__file__).resolve().parents[1]
    probe = f"""
import json, socket
from pathlib import Path

result = {{
    'workspace': Path('/workspace/visible.txt').read_text(),
    'ground_truth_visible': Path({str(project_root / 'tasks_gt')!r}).exists(),
    'source_tasks_visible': Path({str(project_root / 'tasks')!r}).exists(),
    'sibling_tmp_visible': Path({str(forbidden_tmp)!r}).exists(),
    'host_ssh_visible': Path('/home/waynechi/.ssh').exists(),
}}
direct = socket.socket()
direct.settimeout(1)
try:
    direct.connect(('1.1.1.1', 443))
    result['direct_network'] = 'connected'
except OSError:
    result['direct_network'] = 'blocked'
finally:
    direct.close()

proxy = socket.create_connection(('127.0.0.1', 3128), timeout=2)
proxy.sendall(b'CONNECT raw.githubusercontent.com:443 HTTP/1.1\\r\\n\\r\\n')
result['github_proxy'] = proxy.recv(1024).decode('ascii').split()[1]
proxy.close()
print(json.dumps(result))
"""

    proxy_socket = proxy_dir / "provider.sock"
    with ProviderProxy(proxy_socket, ["meta.ai"]) as provider_proxy:
        command = build_bwrap_command(
            agent="muse",
            workspace=workspace,
            private_home=private_home,
            output_dir=output_dir,
            proxy_dir=proxy_dir,
            worker_config=output_dir / "config.json",
            worker_output=output_dir / "result.json",
            use_private_display=False,
            godot_path="godot",
            inner_command=["/usr/bin/python3", "-c", probe],
        )
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )

    result = json.loads(completed.stdout.strip().splitlines()[-1])
    assert result == {
        "workspace": "workspace-only",
        "ground_truth_visible": False,
        "source_tasks_visible": False,
        "sibling_tmp_visible": False,
        "host_ssh_visible": False,
        "direct_network": "blocked",
        "github_proxy": "403",
    }
    assert provider_proxy.audit.denied == ["raw.githubusercontent.com:443"]


@pytest.mark.skipif(
    platform.system() != "Linux"
    or shutil.which("bwrap") is None
    or shutil.which("godot") is None,
    reason="Confined Godot integration test requires Linux, Bubblewrap, and Godot",
)
def test_validation_godot_has_no_host_files_credentials_or_network(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    forbidden = tmp_path / "host-secret.txt"
    forbidden.write_text("secret", encoding="utf-8")
    (workspace / "project.godot").write_text(
        """[application]
run/main_scene="res://main.tscn"

[display]
window/size/viewport_width=320
window/size/viewport_height=240

[rendering]
renderer/rendering_method="gl_compatibility"
""",
        encoding="utf-8",
    )
    (workspace / "main.tscn").write_text(
        """[gd_scene load_steps=2 format=3]

[ext_resource type="Script" path="res://probe.gd" id="1"]

[node name="Probe" type="Node"]
script = ExtResource("1")
""",
        encoding="utf-8",
    )
    (workspace / "probe.gd").write_text(
        f"""extends Node

func _ready():
    var peer = StreamPeerTCP.new()
    peer.connect_to_host("1.1.1.1", 443)
    for step in range(20):
        peer.poll()
        OS.delay_msec(10)
    print("GDB_PROBE:", JSON.stringify({{
        "host_visible": FileAccess.file_exists({str(forbidden)!r}),
        "credentials_visible": OS.has_environment("OPENAI_API_KEY"),
        "network_connected": peer.get_status() == StreamPeerTCP.STATUS_CONNECTED,
    }}))
    get_tree().quit()
""",
        encoding="utf-8",
    )

    completed = run_confined_godot(
        workspace=workspace,
        godot_path="godot",
        godot_args=["--headless", "--path", "/workspace"],
        use_private_display=False,
        timeout_seconds=30,
    )

    probe_line = next(
        line.split("GDB_PROBE:", 1)[1]
        for line in (completed.stdout + completed.stderr).splitlines()
        if "GDB_PROBE:" in line
    )
    assert json.loads(probe_line) == {
        "host_visible": False,
        "credentials_visible": False,
        "network_connected": False,
    }


def test_runner_skips_validation_when_confinement_fails_closed(
    monkeypatch, tmp_path
):
    tasks_dir = tmp_path / "tasks"
    task_dir = tasks_dir / "task_0001"
    task_dir.mkdir(parents=True)
    (task_dir / "project.godot").write_text("config_version=5", encoding="utf-8")
    (task_dir / "task_config.json").write_text(
        json.dumps({"instruction": "test"}), encoding="utf-8"
    )

    monkeypatch.setattr(
        benchmark_runner, "validate_confinement_available", lambda: "test-bwrap"
    )
    runner = GodotBenchmarkRunner(
        use_gt=False,
        agent="muse",
        model="muse-spark-1.2",
        confinement="strict",
    )
    runner.tasks_dir = tasks_dir
    runner.test_results_dir = tasks_dir / "test_result"
    monkeypatch.setattr(
        runner,
        "_run_godot_process",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, "", ""),
    )

    monkeypatch.setattr(
        benchmark_runner,
        "run_confined_solver",
        lambda **kwargs: (_ for _ in ()).throw(ConfinementError("probe failed")),
    )
    validation_called = False

    def unexpected_validation(*args, **kwargs):
        nonlocal validation_called
        validation_called = True
        raise AssertionError("validation must not run after confinement failure")

    monkeypatch.setattr(runner, "_validate_in_directory", unexpected_validation)
    result_dir = tmp_path / "saved-result"
    result_dir.mkdir()
    monkeypatch.setattr(runner, "_save_test_result", lambda *args: result_dir)

    result = runner._run_benchmark_with_agent("task_0001")

    assert not validation_called
    assert not result["success"]
    assert result["confinement"]["status"] == "failed-closed"


@pytest.mark.parametrize("kind", ["symlink", "fifo"])
def test_solver_workspace_rejects_links_and_special_files(tmp_path, kind):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    if kind == "symlink":
        (workspace / "escape").symlink_to("/etc/passwd")
    else:
        os.mkfifo(workspace / "escape")

    with pytest.raises(ConfinementError, match="forbidden"):
        GodotBenchmarkRunner._assert_safe_solver_workspace(workspace)


def test_unsafe_result_tree_is_not_copied(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "escape").symlink_to("/etc/passwd")
    runner = GodotBenchmarkRunner(use_gt=False, confinement="strict")
    runner.test_results_dir = tmp_path / "results"

    saved = runner._save_test_result(
        source,
        "task_0001",
        validation_result=ValidationResult(False, "failed closed"),
        confinement_metadata={"status": "failed-closed"},
        copy_task_files=False,
    )

    assert not (saved / "escape").exists()
    assert json.loads((saved / "result.json").read_text())["confinement"] == {
        "status": "failed-closed"
    }
