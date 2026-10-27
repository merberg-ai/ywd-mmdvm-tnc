"""On-demand FM1 inventory UI with gated private-LAN access; no firmware writes."""
from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
import json
import os
from pathlib import Path
import secrets
import sys
import threading
import time

from .config import StudioConfig, StudioConfigError, load, validate
from .security import PasswordVerifier, SecurityError, initialize_interactive
from .inventory import inventory
from .broker import fetch as fetch_inventory_broker


@dataclass
class Session:
    csrf: str
    expires: float


class AuthState:
    def __init__(self, verifier: PasswordVerifier, *, session_minutes: int = 30):
        self.verifier = verifier
        self.timeout = session_minutes * 60
        self.lock = threading.RLock()
        self.sessions: dict[str, Session] = {}
        self.failures: dict[str, list[float]] = {}

    def blocked(self, address: str) -> bool:
        with self.lock:
            now = time.monotonic()
            attempts = [t for t in self.failures.get(address, []) if now - t < 300]
            self.failures[address] = attempts
            return len(attempts) >= 5

    def attempt(self, address: str, password: str) -> tuple[str, str] | None:
        if self.blocked(address):
            return None
        valid = self.verifier.verify(password)
        with self.lock:
            if not valid:
                self.failures.setdefault(address, []).append(time.monotonic())
                return None
            self.failures.pop(address, None)
            self._purge()
            if len(self.sessions) >= 64:
                return None
            token = secrets.token_urlsafe(32)
            csrf = secrets.token_urlsafe(32)
            self.sessions[token] = Session(csrf=csrf, expires=time.monotonic() + self.timeout)
            return token, csrf

    def _purge(self) -> None:
        now = time.monotonic()
        for key in list(self.sessions):
            if self.sessions[key].expires <= now:
                del self.sessions[key]

    def get(self, token: str) -> Session | None:
        with self.lock:
            self._purge()
            return self.sessions.get(token)

    def logout(self, token: str) -> None:
        with self.lock:
            self.sessions.pop(token, None)


class FirmwareStudioServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, config: StudioConfig, verifier: PasswordVerifier):
        self.config = config
        self.auth = AuthState(verifier, session_minutes=config.session_minutes)
        super().__init__((config.listen, config.port), FirmwareStudioHandler)


class FirmwareStudioHandler(BaseHTTPRequestHandler):
    server: FirmwareStudioServer
    protocol_version = "HTTP/1.1"
    MAX_BODY = 2048

    def log_message(self, format: str, *args) -> None:
        # BaseHTTPRequestHandler normally logs the full request URI, which can
        # contain sensitive query strings. Studio logs no raw HTTP request data.
        return

    def _respond(self, status: int, data: bytes, kind: str, cookie: str = "") -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy",
                         "default-src 'none'; script-src 'self'; style-src 'self'; "
                         "connect-src 'self'; img-src 'self'; base-uri 'none'; "
                         "form-action 'none'; frame-ancestors 'none'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _json(self, status: int, obj: dict, cookie: str = "") -> None:
        self._respond(status, json.dumps(obj, separators=(",", ":")).encode(), "application/json; charset=utf-8", cookie)

    def _trusted_host(self) -> bool:
        expected = {f"{self.server.config.listen}:{self.server.server_port}"}
        if self.server.config.listen == "127.0.0.1":
            expected.add(f"localhost:{self.server.server_port}")
        return self.headers.get("Host", "") in expected

    def _origin_ok(self) -> bool:
        host = self.headers.get("Host", "")
        return self.headers.get("Origin", "") == f"http://{host}"

    def _session_cookie(self) -> str:
        raw = self.headers.get("Cookie", "")
        if len(raw) > 4096:
            return ""
        try:
            jar = SimpleCookie()
            jar.load(raw)
            entry = jar.get("fws_session")
            return entry.value if entry else ""
        except Exception:
            return ""

    def _session(self) -> Session | None:
        return self.server.auth.get(self._session_cookie())

    def _read_json(self) -> dict | None:
        try:
            n = int(self.headers.get("Content-Length", ""))
            if not 1 <= n <= self.MAX_BODY or self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json":
                return None
            obj = json.loads(self.rfile.read(n))
            return obj if isinstance(obj, dict) else None
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            return None

    def do_GET(self) -> None:
        if not self._trusted_host():
            self._json(421, {"error": "untrusted host"})
            return
        assets = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/static/app.js": ("app.js", "application/javascript; charset=utf-8"),
            "/static/style.css": ("style.css", "text/css; charset=utf-8"),
        }
        if self.path in assets:
            name, kind = assets[self.path]
            self._respond(200, files("ywdfws").joinpath("static", name).read_bytes(), kind)
        elif self.path == "/api/session":
            session = self._session()
            if session is None:
                self._json(401, {"authenticated": False})
            else:
                self._json(200, {"authenticated": True, "csrf": session.csrf})
        elif self.path == "/api/inventory":
            if self._session() is None:
                self._json(401, {"error": "authentication required"})
            else:
                try:
                    try:
                        report = fetch_inventory_broker()
                    except (OSError, ValueError, TypeError, json.JSONDecodeError):
                        report = inventory()
                        report["catalog_access"] = "unprivileged-local"
                    self._json(200, report)
                except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                    self._json(503, {"error": "inventory unavailable; check installed manifests"})
        elif self.path == "/api/status":
            if self._session() is None:
                self._json(401, {"error": "authentication required"})
            else:
                self._json(200, {"product": "YWD Firmware Studio", "phase": "FM1",
                                 "hardware_access": False, "firmware_operations": False,
                                 "inventory_available": True,
                                 "flash_enabled": False, "stock_builder_enabled": False})
        else:
            self._json(404, {"error": "unknown endpoint"})

    def do_POST(self) -> None:
        if not self._trusted_host():
            self._json(421, {"error": "untrusted host"})
            return
        if not self._origin_ok():
            self._json(403, {"error": "origin rejected"})
            return
        if self.path == "/api/login":
            if self.server.auth.blocked(self.client_address[0]):
                self._json(429, {"error": "too many attempts; try again later"})
                return
            body = self._read_json()
            if body is None or not isinstance(body.get("password"), str):
                self._json(400, {"error": "invalid request"})
                return
            result = self.server.auth.attempt(self.client_address[0], body["password"])
            if result is None:
                self._json(401, {"error": "invalid credentials"})
                return
            token, csrf = result
            cookie = f"fws_session={token}; Path=/; HttpOnly; SameSite=Strict"
            self._json(200, {"authenticated": True, "csrf": csrf}, cookie)
        elif self.path == "/api/logout":
            token = self._session_cookie()
            session = self.server.auth.get(token)
            if not session:
                self._json(401, {"error": "authentication required"})
            elif not secrets.compare_digest(self.headers.get("X-CSRF-Token", ""), session.csrf):
                self._json(403, {"error": "CSRF verification failed"})
            else:
                self.server.auth.logout(token)
                self._json(200, {"authenticated": False},
                           "fws_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict")
        else:
            self._json(404, {"error": "operation not available in FM0"})

    def do_HEAD(self) -> None:
        self.do_GET()


def create_server(config: StudioConfig, verifier: PasswordVerifier) -> FirmwareStudioServer:
    return FirmwareStudioServer(config, verifier)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="On-demand YWD Firmware Studio FM1")
    parser.add_argument("--config", default="config/ywd-fws.example.toml")
    parser.add_argument("--listen", default=None,
                        help="override listener with an exact IPv4 address")
    parser.add_argument("--port", type=int, default=None,
                        help="override HTTP port")
    parser.add_argument("--allow-private-bind", action="store_true",
                        help="explicitly authorize RFC1918 private LAN listener for VPN testing")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--init-password", action="store_true")
    group.add_argument("--check", action="store_true")
    group.add_argument("--check-inventory", action="store_true",
                       help="diagnose reference profiles and backup visibility, never touch HAT")
    args = parser.parse_args(argv)
    try:
        config = load(args.config)
        config = validate(replace(
            config,
            listen=args.listen if args.listen is not None else config.listen,
            port=args.port if args.port is not None else config.port,
            allow_private_bind=config.allow_private_bind or args.allow_private_bind,
        ))
        if os.geteuid() == 0:
            raise SecurityError("Do not run the web server or password setup as root")
        if args.init_password:
            initialize_interactive(config.password_file)
            print("FWS_PASSWORD_INITIALIZED=YES")
            return 0
        verifier = PasswordVerifier(config.password_file)
        if args.check_inventory:
            report = inventory()
            print("FWS_INVENTORY=PASS")
            print(f"FWS_REFERENCE_TARGET={report['target']['id']}")
            print(f"FWS_BACKUP_ACCESS={report['backup_access'].upper()}")
            print(f"FWS_BACKUP_COUNT={report['backup_count']}")
            print("FWS_LIVE_PROBE=NO")
            print("FWS_HARDWARE_ACCESS=NO")
            return 0
        if args.check:
            print("FWS_CONFIG=PASS")
            print("FWS_AUTH=PASS")
            print("FWS_HARDWARE_ACCESS=NO")
            print("FWS_FLASH_ENABLED=NO")
            return 0
        server = create_server(config, verifier)
        if config.allow_private_bind and config.listen != "127.0.0.1":
            print("WARNING: HTTP on the LAN is unencrypted. WireGuard encrypts only its tunnel segment;")
            print("         restrict access to trusted clients. No firmware-write API exists in FM1.")
        print(f"Firmware Studio FM1: http://{config.listen}:{server.server_port}/")
        print("Read-only firmware inventory; no browser hardware operations. Stop with Ctrl-C.")
        try:
            server.serve_forever(poll_interval=0.25)
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return 0
    except (StudioConfigError, SecurityError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"FWS_ERROR={exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
