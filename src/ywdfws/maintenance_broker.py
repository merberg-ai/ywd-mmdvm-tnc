"""FM1.5c on-demand, root-only maintenance broker.

One allowlisted operation: two-pass BACKUP of qualified main flash.
NEVER accepts shell commands, paths, firmware uploads, flash/write/restore,
device selection, or service names from the unprivileged web process.

This service must be explicitly started by root from a real operator TTY.
A one-use code shown ONLY in that terminal authorizes a single backup.
It cannot be started or armed remotely by an HTTP call.
"""
from __future__ import annotations

import argparse
import hmac
import json
import os
from pathlib import Path
import pwd
import secrets
import socket
import socketserver
import stat
import struct
import sys
import threading
import time

SOCKET_PATH = Path("/run/ywd-fws-maintenance.sock")
AUDIT_PATH = Path("/var/lib/ywd-mmdvm-tnc/fws-maintenance-audit.jsonl")
CONFIRM_PHRASE = "READ-HAT-MAIN-FLASH"
CODE_LIFETIME = 600
MAX_REQUEST = 512
MAX_RESPONSE = 32768


class MaintenanceError(ValueError):
    pass


class BackupController:
    def __init__(self, *, executor=None, audit_path: Path = AUDIT_PATH,
                 lifetime: int = CODE_LIFETIME, enabled: bool = False):
        self.lock = threading.RLock()
        self.enabled = enabled
        self.code = secrets.token_urlsafe(24) if enabled else ""
        self.expires = time.monotonic() + lifetime
        self.failures = 0
        self.job: dict | None = None
        self.audit_path = audit_path
        self.executor = executor or _real_backup

    def _audit(self, event: str, **detail) -> None:
        self.audit_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        if self.audit_path.is_symlink():
            raise MaintenanceError("audit log must not be a symlink")
        fd = os.open(self.audit_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT |
                     getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            os.fchmod(fd, 0o600)
            data = json.dumps({"time": int(time.time()), "event": event,
                               **detail}, separators=(",", ":")) + "\n"
            os.write(fd, data.encode())
            os.fsync(fd)
        finally:
            os.close(fd)

    def ready(self) -> dict:
        with self.lock:
            armed = bool(self.code and time.monotonic() < self.expires)
            return {"schema": 1, "operation": "backup",
                    "armed": armed, "enabled": self.enabled,
                    "busy": self.job is not None and self.job["status"] == "running",
                    "transport": "AF_UNIX", "flash_allowed": False}

    def _event(self, message: str, stage: str) -> None:
        with self.lock:
            if not self.job:
                return
            self.job["stage"] = stage
            events = self.job["events"]
            if len(events) < 64:
                events.append({"seq": len(events) + 1, "time": int(time.time()),
                               "message": message})
            self.job["steps_completed"] = {
                "starting": 0, "preflight": 1, "service_stop": 2,
                "probe": 3, "read_a": 4, "read_b": 5,
                "verify": 6, "recovery": 7, "complete": 8,
            }.get(stage, self.job["steps_completed"])

    def begin(self, approval: str, confirmation: str) -> dict:
        with self.lock:
            if not self.enabled or not self.code or time.monotonic() >= self.expires:
                raise MaintenanceError("maintenance worker not armed; restart it at an operator terminal")
            if self.job is not None:
                raise MaintenanceError("this worker has already handled its one permitted backup job")
            if self.failures >= 5:
                self.code = ""
                raise MaintenanceError("authorization locked; restart broker at a root terminal")
            if not (isinstance(approval, str) and
                    hmac.compare_digest(approval, self.code) and
                    confirmation == CONFIRM_PHRASE):
                self.failures += 1
                if self.failures >= 5:
                    self.code = ""
                raise MaintenanceError("authorization denied")
            # Fail closed if the durable audit cannot be initialized.
            self._audit("BACKUP_AUTHORIZED", mode="two-pass-read-only")
            self.code = ""  # one use only; cannot be retried without operator restart
            identifier = secrets.token_urlsafe(18)
            self.job = {"schema": 1, "id": identifier, "status": "running",
                        "stage": "starting", "steps_total": 8,
                        "steps_completed": 0, "events": [],
                        "backup_kind": None, "sha256": None,
                        "hardware_access": True, "flash_written": False,
                        "option_bytes_written": False}
            self._event("Operator-authorized two-pass backup queued", "starting")
            threading.Thread(target=self._run, name="fws-two-pass-backup",
                             daemon=False).start()
            return self.snapshot(identifier)

    def snapshot(self, identifier: str) -> dict | None:
        with self.lock:
            if not self.job or not isinstance(identifier, str) or not hmac.compare_digest(identifier, self.job["id"]):
                return None
            return {**self.job, "events": list(self.job["events"])}

    def _run(self) -> None:
        def progress(message: str, stage: str) -> None:
            self._event(message, stage)
        try:
            outcome = self.executor(progress)
            if not isinstance(outcome, dict) or outcome.get("verified") is not True:
                raise MaintenanceError("two-pass verification result was not successful")
            with self.lock:
                self.job["status"] = "completed"
                self.job["backup_kind"] = outcome["kind"]
                self.job["sha256"] = outcome["sha256"]
                self._event("Two-pass main-flash backup verified", "complete")
            self._audit("BACKUP_COMPLETED", kind=outcome["kind"], sha256=outcome["sha256"])
        except Exception as exc:
            with self.lock:
                self.job["status"] = "failed"
                self._event("Backup failed; inspect root worker terminal and modem service status", "recovery")
            try:
                self._audit("BACKUP_FAILED", error_type=type(exc).__name__)
            except OSError:
                pass
            print(f"FWS_BACKUP_FAILED={type(exc).__name__}: {exc}", file=sys.stderr, flush=True)


def _real_backup(progress):
    # Only the privileged broker imports the qualified HAT control module.
    # This path is never imported or invoked by the HTTP server.
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "firmware"))
    import fws_backup
    return fws_backup.perform_backup(progress=progress, authorized_by_broker=True)


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        self.request.settimeout(4)
        try:
            credential = self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            _, uid, _ = struct.unpack("3i", credential)
            if uid != self.server.allowed_uid:
                return
            raw = self.rfile.readline(MAX_REQUEST + 2)
            if len(raw) > MAX_REQUEST or not raw.endswith(b"\n"):
                return
            item = json.loads(raw)
            if not isinstance(item, dict):
                return
            action = item.get("op")
            if action == "READY" and set(item) == {"op"}:
                response = self.server.controller.ready()
            elif action == "START_BACKUP" and set(item) == {"op", "authorization", "confirmation"}:
                try:
                    response = self.server.controller.begin(item["authorization"], item["confirmation"])
                except MaintenanceError as exc:
                    response = {"error": str(exc)}
            elif action == "STATUS" and set(item) == {"op", "id"}:
                response = self.server.controller.snapshot(item["id"]) or {"error": "unknown job"}
            else:
                response = {"error": "unsupported operation"}
            serialized = json.dumps(response, separators=(",", ":")).encode()
            if len(serialized) > MAX_RESPONSE:
                response = {"error": "response too large"}
                serialized = json.dumps(response).encode()
            self.wfile.write(serialized + b"\n")
            self.wfile.flush()
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return


class MaintenanceServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, path: Path, uid: int, gid: int, controller: BackupController):
        if os.geteuid() != 0:
            raise PermissionError("maintenance broker must run as root")
        if uid == 0:
            raise MaintenanceError("refusing root browser account")
        if path != SOCKET_PATH:
            raise MaintenanceError("maintenance socket path is fixed")
        if path.exists() or path.is_symlink():
            raise MaintenanceError("maintenance socket already exists; do not replace active broker")
        self.allowed_uid = uid
        self.controller = controller
        super().__init__(str(path), _Handler)
        os.chown(path, uid, gid)
        os.chmod(path, 0o600)


def request(op: str, *, authorization: str = "", confirmation: str = "",
            job_id: str = "", path: Path = SOCKET_PATH) -> dict:
    if op == "READY":
        command = {"op": op}
    elif op == "START_BACKUP":
        command = {"op": op, "authorization": authorization, "confirmation": confirmation}
    elif op == "STATUS":
        command = {"op": op, "id": job_id}
    else:
        raise MaintenanceError("unsupported maintenance command")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
        channel.settimeout(4)
        channel.connect(str(path))
        channel.sendall(json.dumps(command, separators=(",", ":")).encode() + b"\n")
        data = b""
        while not data.endswith(b"\n"):
            part = channel.recv(4096)
            if not part or len(data) + len(part) > MAX_RESPONSE:
                raise MaintenanceError("invalid maintenance worker response")
            data += part
    result = json.loads(data)
    if not isinstance(result, dict):
        raise MaintenanceError("invalid maintenance result")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="FM1.5c on-demand root backup worker; ONE operation")
    parser.add_argument("--web-user", required=True)
    parser.add_argument("--enable-backup", action="store_true")
    args = parser.parse_args()
    try:
        if os.geteuid() != 0 or not sys.stdin.isatty() or not sys.stdout.isatty():
            raise MaintenanceError("must be started as root at a real, interactive operator terminal")
        if not args.enable_backup:
            raise MaintenanceError("explicit --enable-backup required")
        account = pwd.getpwnam(args.web_user)
        controller = BackupController(enabled=True)
        controller._audit("BROKER_ARMED", mode="single-use-backup")
        with MaintenanceServer(SOCKET_PATH, account.pw_uid, account.pw_gid, controller) as server:
            print("FWS_MAINTENANCE_BROKER=ARMED")
            print("WARNING: Authorized backup will briefly stop modem services and reset the HAT.")
            print("This worker permits exactly ONE main-flash backup. It NEVER flashes firmware.")
            print(f"FWS_ONE_TIME_APPROVAL={controller.code}")
            print("Approval expires in 10 minutes. Never paste it into logs or GitHub.")
            print("When the job is completed, terminate this broker with Ctrl+C.", flush=True)
            try:
                server.serve_forever(poll_interval=.2)
            finally:
                controller.code = ""
                server.server_close()
                SOCKET_PATH.unlink(missing_ok=True)
        return 0
    except (MaintenanceError, OSError, KeyError, ValueError) as exc:
        print(f"FWS_MAINTENANCE_ERROR={exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
