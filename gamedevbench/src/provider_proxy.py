#!/usr/bin/env python3
"""Provider-only network proxy used by the solver confinement layer.

The host side accepts HTTP CONNECT requests over a Unix socket and permits
only explicitly configured provider domains on port 443.  A tiny relay inside
the solver network namespace exposes that Unix socket as a loopback HTTP
proxy.  The namespace itself has no route to the host or Internet.
"""

from __future__ import annotations

import argparse
import ipaddress
import socket
import socketserver
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Sequence


MAX_HEADER_BYTES = 64 * 1024
MAX_CLIENT_HELLO_BYTES = 256 * 1024
BUFFER_SIZE = 64 * 1024


class NonPublicAddressError(OSError):
    """Raised when an allowlisted name resolves only to unsafe addresses."""


def normalize_host(host: str) -> str:
    """Normalize and validate a DNS hostname used in a CONNECT request."""
    candidate = host.strip().rstrip(".").lower()
    if not candidate or "\x00" in candidate:
        raise ValueError("empty or invalid hostname")
    try:
        candidate = candidate.encode("idna").decode("ascii")
    except UnicodeError as error:
        raise ValueError("invalid IDNA hostname") from error
    if any(not label or len(label) > 63 for label in candidate.split(".")):
        raise ValueError("invalid DNS label")
    allowed_chars = set("abcdefghijklmnopqrstuvwxyz0123456789-.")
    if any(character not in allowed_chars for character in candidate):
        raise ValueError("non-DNS hostname")
    return candidate


def host_is_allowed(host: str, allowed_suffixes: Iterable[str]) -> bool:
    """Return whether *host* exactly matches or is below an allowed suffix."""
    try:
        normalized = normalize_host(host)
    except ValueError:
        return False
    for suffix in allowed_suffixes:
        try:
            normalized_suffix = normalize_host(suffix.lstrip("*."))
        except ValueError:
            continue
        if normalized == normalized_suffix or normalized.endswith(
            f".{normalized_suffix}"
        ):
            return True
    return False


def _connect_public_host(host: str, port: int, timeout: float) -> socket.socket:
    """Resolve once and connect only to a globally routable address."""
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    safe_addresses = []
    for family, socktype, protocol, canonical_name, sockaddr in addresses:
        del canonical_name
        try:
            address = ipaddress.ip_address(sockaddr[0].split("%", 1)[0])
        except ValueError:
            continue
        if address.is_global:
            safe_addresses.append((family, socktype, protocol, sockaddr))

    if not safe_addresses:
        raise NonPublicAddressError(
            f"Refusing non-public address resolution for {host}"
        )

    last_error = None
    for family, socktype, protocol, sockaddr in safe_addresses:
        upstream = socket.socket(family, socktype, protocol)
        upstream.settimeout(timeout)
        try:
            upstream.connect(sockaddr)
            return upstream
        except OSError as error:
            last_error = error
            upstream.close()
    raise last_error or OSError(f"Could not connect to {host}:{port}")


def _parse_client_hello_sni(data: bytes) -> str | None:
    """Return SNI, an empty string if absent, or None for incomplete input."""
    handshake = bytearray()
    record_cursor = 0
    while True:
        if len(data) < record_cursor + 5:
            return None
        content_type = data[record_cursor]
        record_length = int.from_bytes(
            data[record_cursor + 3 : record_cursor + 5], "big"
        )
        record_end = record_cursor + 5 + record_length
        if len(data) < record_end:
            return None
        if content_type != 22:  # TLS handshake
            raise ValueError("expected a TLS ClientHello")
        handshake.extend(data[record_cursor + 5 : record_end])
        record_cursor = record_end
        if len(handshake) < 4:
            continue
        if handshake[0] != 1:  # client_hello
            raise ValueError("expected a TLS ClientHello")
        hello_length = int.from_bytes(handshake[1:4], "big")
        if len(handshake) < 4 + hello_length:
            continue
        break

    hello = memoryview(handshake)[4 : 4 + hello_length]
    cursor = 34  # legacy_version plus random
    if len(hello) < cursor + 1:
        raise ValueError("truncated TLS ClientHello")

    session_id_length = hello[cursor]
    cursor += 1 + session_id_length
    if len(hello) < cursor + 2:
        raise ValueError("truncated TLS ClientHello")
    cipher_length = int.from_bytes(hello[cursor : cursor + 2], "big")
    cursor += 2 + cipher_length
    if len(hello) < cursor + 1:
        raise ValueError("truncated TLS ClientHello")
    compression_length = hello[cursor]
    cursor += 1 + compression_length
    if len(hello) == cursor:
        return ""
    if len(hello) < cursor + 2:
        raise ValueError("truncated TLS extensions")
    extensions_length = int.from_bytes(hello[cursor : cursor + 2], "big")
    cursor += 2
    extensions_end = cursor + extensions_length
    if extensions_end > len(hello):
        raise ValueError("truncated TLS extensions")

    while cursor + 4 <= extensions_end:
        extension_type = int.from_bytes(hello[cursor : cursor + 2], "big")
        extension_length = int.from_bytes(hello[cursor + 2 : cursor + 4], "big")
        cursor += 4
        extension_end = cursor + extension_length
        if extension_end > extensions_end:
            raise ValueError("truncated TLS extension")
        if extension_type == 0:  # server_name
            extension = hello[cursor:extension_end]
            if len(extension) < 2:
                raise ValueError("truncated TLS server_name")
            names_length = int.from_bytes(extension[:2], "big")
            name_cursor = 2
            names_end = 2 + names_length
            if names_end > len(extension):
                raise ValueError("truncated TLS server_name")
            while name_cursor + 3 <= names_end:
                name_type = extension[name_cursor]
                name_length = int.from_bytes(
                    extension[name_cursor + 1 : name_cursor + 3], "big"
                )
                name_cursor += 3
                name_end = name_cursor + name_length
                if name_end > names_end:
                    raise ValueError("truncated TLS server_name")
                if name_type == 0:
                    try:
                        encoded_name = bytes(extension[name_cursor:name_end])
                        return normalize_host(encoded_name.decode("ascii"))
                    except UnicodeDecodeError as error:
                        raise ValueError("invalid TLS server_name") from error
                name_cursor = name_end
            return ""
        cursor = extension_end
    return ""


def _receive_client_hello(client: socket.socket) -> tuple[str, bytes]:
    """Receive and validate the first TLS handshake without consuming it."""
    data = bytearray()
    while len(data) < MAX_CLIENT_HELLO_BYTES:
        chunk = client.recv(min(16 * 1024, MAX_CLIENT_HELLO_BYTES - len(data)))
        if not chunk:
            raise ValueError("connection closed before TLS ClientHello")
        data.extend(chunk)
        server_name = _parse_client_hello_sni(bytes(data))
        if server_name is not None:
            return server_name, bytes(data)
    raise ValueError("TLS ClientHello is too large")


@dataclass
class ProxyAudit:
    """Thread-safe audit trail for provider proxy decisions."""

    allowed: List[str] = field(default_factory=list)
    denied: List[str] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record(self, destination: str, permitted: bool) -> None:
        with self._lock:
            (self.allowed if permitted else self.denied).append(destination)

    def to_dict(self) -> dict:
        with self._lock:
            return {
                "allowed_connects": list(self.allowed),
                "denied_connects": list(self.denied),
            }


def _relay_bidirectional(left: socket.socket, right: socket.socket) -> None:
    """Relay bytes until either side closes."""
    def pump(source: socket.socket, destination: socket.socket) -> None:
        try:
            while True:
                try:
                    data = source.recv(BUFFER_SIZE)
                except InterruptedError:
                    continue
                if not data:
                    return
                destination.sendall(data)
        except OSError:
            return
        finally:
            try:
                destination.shutdown(socket.SHUT_WR)
            except OSError:
                pass

    outbound = threading.Thread(target=pump, args=(left, right), daemon=True)
    outbound.start()
    pump(right, left)
    outbound.join(timeout=5.0)


class _ProviderProxyHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        server = self.server
        header = bytearray()
        self.request.settimeout(15.0)
        while b"\r\n\r\n" not in header and len(header) < MAX_HEADER_BYTES:
            chunk = self.request.recv(4096)
            if not chunk:
                return
            header.extend(chunk)

        first_line = bytes(header).split(b"\r\n", 1)[0]
        try:
            method, authority, _ = first_line.decode("ascii").split(" ", 2)
            if method.upper() != "CONNECT":
                raise ValueError("only CONNECT is supported")
            host, port_text = authority.rsplit(":", 1)
            host = normalize_host(host)
            port = int(port_text)
        except (UnicodeDecodeError, ValueError):
            server.audit.record("invalid-request", False)
            self.request.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
            return

        destination = f"{host}:{port}"
        if port != 443 or not host_is_allowed(host, server.allowed_hosts):
            server.audit.record(destination, False)
            self.request.sendall(b"HTTP/1.1 403 Forbidden\r\n\r\n")
            return

        try:
            upstream = _connect_public_host(host, port, timeout=15.0)
        except NonPublicAddressError:
            server.audit.record(destination, False)
            self.request.sendall(b"HTTP/1.1 403 Forbidden\r\n\r\n")
            return
        except OSError:
            server.audit.record(destination, True)
            self.request.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            return

        with upstream:
            self.request.sendall(
                b"HTTP/1.1 200 Connection Established\r\n"
                b"Proxy-Agent: gamedevbench-provider-proxy\r\n\r\n"
            )
            try:
                server_name, client_hello = _receive_client_hello(self.request)
            except (OSError, ValueError):
                server.audit.record(f"{destination} (invalid TLS ClientHello)", False)
                return
            if server_name != host:
                server.audit.record(
                    f"{destination} (TLS SNI {server_name or 'missing'})", False
                )
                return
            server.audit.record(destination, True)
            upstream.sendall(client_hello)
            self.request.settimeout(None)
            upstream.settimeout(None)
            _relay_bidirectional(self.request, upstream)


class _ThreadingUnixServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True


class ProviderProxy:
    """Host-side provider allowlist proxy exposed over a Unix socket."""

    def __init__(self, socket_path: Path, allowed_hosts: Sequence[str]):
        if not allowed_hosts:
            raise ValueError("at least one provider hostname is required")
        self.socket_path = Path(socket_path)
        self.allowed_hosts = tuple(normalize_host(host) for host in allowed_hosts)
        self.audit = ProxyAudit()
        self._server = None
        self._thread = None

    def __enter__(self) -> "ProviderProxy":
        self.socket_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.socket_path.unlink(missing_ok=True)
        server = _ThreadingUnixServer(
            str(self.socket_path), _ProviderProxyHandler
        )
        server.allowed_hosts = self.allowed_hosts
        server.audit = self.audit
        self._server = server
        self._thread = threading.Thread(target=server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        self.socket_path.unlink(missing_ok=True)


class _UnixRelayHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        unix_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            unix_socket.connect(self.server.unix_socket_path)
            _relay_bidirectional(self.request, unix_socket)
        finally:
            unix_socket.close()


class _ThreadingTcpServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


def run_namespace_relay(
    unix_socket_path: str,
    port: int,
    command: Sequence[str],
) -> int:
    """Expose a mounted Unix proxy on loopback and run *command*."""
    server = _ThreadingTcpServer(("127.0.0.1", port), _UnixRelayHandler)
    server.unix_socket_path = unix_socket_path
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        completed = subprocess.run(list(command), check=False)
        return completed.returncode
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5.0)


def main() -> None:
    parser = argparse.ArgumentParser(description="GameDevBench namespace proxy relay")
    parser.add_argument("--socket", required=True)
    parser.add_argument("--port", type=int, default=3128)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("a command is required after --")
    raise SystemExit(run_namespace_relay(args.socket, args.port, command))


if __name__ == "__main__":
    main()
