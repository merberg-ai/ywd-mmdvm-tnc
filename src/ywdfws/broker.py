"""FM1 local, root-only INVENTORY broker.

A narrow AF_UNIX protocol lets the unprivileged web server request only
metadata/verified backup listings. No GPIO, UART, service or flash operations.
Operator launches broker separately; no permanent systemd unit is installed.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import pwd
import signal
import socket
import stat
import struct
import sys

from .inventory import inventory


DEFAULT_SOCKET = Path("/run/ywd-fws-inventory.sock")
MAX_RESPONSE = 131072


def serve(path: Path, *, web_uid: int, web_gid: int) -> None:
    if os.geteuid() != 0:
        raise PermissionError("inventory broker must run as root")
    if web_uid == 0:
        raise ValueError("refuse to authorize root as browser worker")
    if not path.is_absolute() or path.parent != Path("/run") or path.name != DEFAULT_SOCKET.name:
        raise ValueError("socket path must be /run/ywd-fws-inventory.sock")
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"socket already exists: {path}; stop old broker first")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    previous_umask = os.umask(0o077)
    try:
        sock.bind(str(path))
    finally:
        os.umask(previous_umask)
    try:
        os.chown(path, web_uid, web_gid)
        os.chmod(path, 0o600)
        sock.listen(5)
        sock.settimeout(0.5)
        print("FWS_INVENTORY_BROKER=READY", flush=True)
        print(f"FWS_AUTHORIZED_UID={web_uid}", flush=True)
        print("FWS_HARDWARE_ACCESS=NO", flush=True)
        print("FWS_WRITE_OPERATIONS=NO", flush=True)
        while True:
            try:
                conn, _ = sock.accept()
            except socket.timeout:
                continue
            with conn:
                conn.settimeout(3)
                try:
                    peer = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
                    pid, uid, gid = struct.unpack("3i", peer)
                    if uid != web_uid:
                        continue
                    if conn.recv(32) != b"INVENTORY\n":
                        continue
                    result = inventory()
                    result["catalog_access"] = "privileged-readonly-broker"
                    encoded = json.dumps(result, separators=(",", ":")).encode()
                    if len(encoded) > MAX_RESPONSE:
                        raise ValueError("inventory response exceeds bounded size")
                    conn.sendall(encoded + b"\n")
                except (OSError, ValueError, TypeError, json.JSONDecodeError):
                    continue
    finally:
        sock.close()
        path.unlink(missing_ok=True)


def fetch(path: Path = DEFAULT_SOCKET) -> dict:
    """Unprivileged client; bounded, exact-opcode, read-only request."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(2)
        sock.connect(str(path))
        sock.sendall(b"INVENTORY\n")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = sock.recv(8192)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_RESPONSE + 1:
                raise ValueError("broker response too large")
            chunks.append(chunk)
            if b"\n" in chunk:
                break
        raw = b"".join(chunks)
        if not raw.endswith(b"\n") or raw.count(b"\n") != 1:
            raise ValueError("malformed inventory broker response")
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get("schema") != 1 or data.get("phase") != "FM1":
            raise ValueError("invalid inventory schema from broker")
        return data
    finally:
        sock.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="On-demand root-only read-only backup metadata broker")
    parser.add_argument("--web-user", required=True, help="unprivileged web server account, e.g. ywd")
    args = parser.parse_args()
    try:
        account = pwd.getpwnam(args.web_user)
        serve(DEFAULT_SOCKET, web_uid=account.pw_uid, web_gid=account.pw_gid)
        return 0
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError, KeyError) as exc:
        print(f"FWS_BROKER_ERROR={exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
