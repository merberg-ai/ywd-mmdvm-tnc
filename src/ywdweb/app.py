"""Dependency-free HTTP/SSE server for the passive YWD packet monitor."""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
from queue import Empty
import signal
import threading
import time
from urllib.parse import parse_qs, urlsplit

from .config import WebUIConfigurationError, WebUIConfig, load_config
from .history import HistoryStore, valid_date
from .monitor_client import MonitorReader
from .state import LiveState


STATIC_ROOT = Path(__file__).resolve().parent / "static"


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, separators=(",", ":"), ensure_ascii=True) + "\n").encode("utf-8")


class WebUIApplication:
    def __init__(self, config: WebUIConfig) -> None:
        self.config = config
        self.state = LiveState(
            recent_capacity=config.history.recent_capacity,
            sse_queue_capacity=config.history.sse_queue_capacity,
        )
        self.history = HistoryStore(
            config.history.directory,
            max_history_limit=config.history.max_history_limit,
            max_console_lines=config.history.max_console_lines,
            max_stats_events=config.history.max_stats_events,
        )
        self.monitor = MonitorReader(config.monitor, self.state)
        self.started_monotonic = time.monotonic()

    def status(self) -> dict[str, object]:
        snap = asdict(self.state.snapshot)
        last = snap.pop("last_event_monotonic")
        snap["last_event_age_seconds"] = None if last is None else round(max(0.0, time.monotonic() - float(last)), 3)
        snap["uptime_seconds"] = round(time.monotonic() - self.started_monotonic, 3)
        snap["history_directory"] = str(self.config.history.directory)
        snap["history_dates"] = self.history.dates()
        snap["read_only"] = True
        snap["schema"] = 1
        return snap


class YWDHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], application: WebUIApplication) -> None:
        self.application = application
        super().__init__(address, YWDRequestHandler)


class YWDRequestHandler(BaseHTTPRequestHandler):
    server: YWDHTTPServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        print(f"YWD_WEBUI_HTTP={self.address_string()} {format % args}", flush=True)

    def _headers(self, status: int, content_type: str, length: int | None = None, *, cache: str = "no-store") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        if length is not None:
            self.send_header("Content-Length", str(length))
        self.end_headers()

    def _json(self, value: object, status: int = 200) -> None:
        payload = _json_bytes(value)
        self._headers(status, "application/json; charset=utf-8", len(payload))
        self.wfile.write(payload)

    def _error(self, status: int, message: str) -> None:
        self._json({"error": message, "status": status}, status=status)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlsplit(self.path)
        query = parse_qs(parsed.query, keep_blank_values=False)
        try:
            if parsed.path == "/api/v1/status":
                self._json(self.server.application.status())
                return
            if parsed.path == "/api/v1/recent":
                self._json({"events": self.server.application.state.recent()})
                return
            if parsed.path == "/api/v1/dates":
                self._json({"dates": self.server.application.history.dates()})
                return
            if parsed.path == "/api/v1/history":
                self._history(query)
                return
            if parsed.path == "/api/v1/stats":
                self._stats(query)
                return
            if parsed.path == "/api/v1/human-log":
                self._human_log(query)
                return
            if parsed.path == "/api/v1/stream":
                self._stream()
                return
            if parsed.path.startswith("/api/"):
                self._error(404, "unknown API endpoint")
                return
            self._static(parsed.path)
        except ValueError as exc:
            self._error(400, str(exc))
        except (BrokenPipeError, ConnectionResetError):
            return

    def _arg(self, query: dict[str, list[str]], name: str, default: str | None = None) -> str | None:
        values = query.get(name)
        return values[0] if values else default

    def _date_arg(self, query: dict[str, list[str]]) -> str:
        requested = self._arg(query, "date")
        if requested:
            return valid_date(requested)
        dates = self.server.application.history.dates()
        if dates:
            return dates[0]
        return datetime.now().astimezone().strftime("%Y-%m-%d")

    def _history(self, query: dict[str, list[str]]) -> None:
        date = self._date_arg(query)
        limit = int(self._arg(query, "limit", "250") or "250")
        events = self.server.application.history.events(
            date,
            limit=limit,
            kind=self._arg(query, "event"),
            station=self._arg(query, "station"),
            query=self._arg(query, "q"),
        )
        self._json({"date": date, "events": events, "count": len(events)})

    def _stats(self, query: dict[str, list[str]]) -> None:
        date = self._date_arg(query)
        self._json(self.server.application.history.stats(date))

    def _human_log(self, query: dict[str, list[str]]) -> None:
        date = self._date_arg(query)
        lines = int(self._arg(query, "lines", "400") or "400")
        self._json({"date": date, "lines": self.server.application.history.console(date, lines=lines)})

    def _stream(self) -> None:
        queue = self.server.application.state.subscribe()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(b": ywd-webui passive stream\n\n")
            self.wfile.flush()
            while True:
                try:
                    event = queue.get(timeout=15.0)
                except Empty:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    continue
                generation = event.get("_web_generation", 0)
                seq = event.get("seq", 0)
                payload = json.dumps(event, separators=(",", ":"), ensure_ascii=True)
                block = f"id: {generation}:{seq}\nevent: packet\ndata: {payload}\n\n".encode("utf-8")
                self.wfile.write(block)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
            return
        finally:
            self.server.application.state.unsubscribe(queue)

    def _static(self, request_path: str) -> None:
        relative = "index.html" if request_path in {"", "/"} else request_path.lstrip("/")
        if ".." in Path(relative).parts:
            self._error(404, "not found")
            return
        path = STATIC_ROOT / relative
        if not path.is_file():
            path = STATIC_ROOT / "index.html"
        try:
            payload = path.read_bytes()
        except OSError:
            self._error(404, "static asset unavailable")
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"}:
            content_type += "; charset=utf-8"
        cache = "public, max-age=3600" if path.name != "index.html" else "no-cache"
        self._headers(200, content_type, len(payload), cache=cache)
        self.wfile.write(payload)


def create_server(config: WebUIConfig, *, application: WebUIApplication | None = None) -> tuple[YWDHTTPServer, WebUIApplication]:
    app = application or WebUIApplication(config)
    server = YWDHTTPServer((config.web.listen, config.web.port), app)
    return server, app


def main() -> int:
    parser = argparse.ArgumentParser(prog="ywd-webui")
    parser.add_argument("--config", default="/etc/ywd-mmdvm-tnc/webui.toml")
    parser.add_argument("--framework-self-test", action="store_true")
    args = parser.parse_args()
    try:
        config = load_config(args.config)
    except WebUIConfigurationError as exc:
        print(f"ywd-webui: configuration error: {exc}", flush=True)
        return 2
    if args.framework_self_test:
        print("YWD_WEBUI_FRAMEWORK_SELF_TEST=PASS")
        print("MONITOR_SOCKET_OPENED=NO")
        print("KISS_SOCKET_OPENED=NO")
        print("MODEM_UART_OPENED=NO")
        print("RF_TRANSMITTED=NO")
        return 0

    server, app = create_server(config)
    stopping = threading.Event()

    def request_stop(signum, frame) -> None:  # type: ignore[no-untyped-def]
        _ = signum, frame
        if not stopping.is_set():
            stopping.set()
            threading.Thread(target=server.shutdown, name="ywd-webui-shutdown", daemon=True).start()

    old_int = signal.getsignal(signal.SIGINT)
    old_term = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    app.monitor.start()
    host, port = server.server_address[:2]
    print(f"YWD_WEBUI=RUNNING http://{host}:{port}", flush=True)
    print(f"YWD_WEBUI_MONITOR_SOURCE={config.monitor.host}:{config.monitor.port}", flush=True)
    print(f"YWD_WEBUI_HISTORY={config.history.directory}", flush=True)
    print("YWD_WEBUI_READ_ONLY=YES", flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        app.monitor.stop()
        server.server_close()
        signal.signal(signal.SIGINT, old_int)
        signal.signal(signal.SIGTERM, old_term)
        print("YWD_WEBUI=STOPPED", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
