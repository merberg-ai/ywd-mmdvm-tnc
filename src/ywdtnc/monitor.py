"""Passive NDJSON monitor stream for YWD-MMDVM-TNC."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from queue import Empty, Full, Queue
import socketserver
import threading
from typing import Callable

from ywd1278.ax25 import parse_frame

SCHEMA_VERSION = 1
TimestampFactory = Callable[[], str]


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class MonitorSnapshot:
    subscribers: int
    published: int
    subscriber_drops: int
    encoding_errors: int


class MonitorEventHub:
    """Non-blocking fan-out with bounded subscriber queues and no history."""

    def __init__(self, *, queue_capacity: int = 256, timestamp_factory: TimestampFactory = utc_timestamp) -> None:
        if queue_capacity < 1:
            raise ValueError("queue_capacity must be at least 1")
        self._capacity = int(queue_capacity)
        self._timestamp_factory = timestamp_factory
        self._subscribers: set[Queue[bytes]] = set()
        self._lock = threading.Lock()
        self._sequence = 0
        self._published = 0
        self._drops = 0
        self._encoding_errors = 0

    @property
    def snapshot(self) -> MonitorSnapshot:
        with self._lock:
            return MonitorSnapshot(len(self._subscribers), self._published, self._drops, self._encoding_errors)

    def subscribe(self) -> Queue[bytes]:
        queue: Queue[bytes] = Queue(maxsize=self._capacity)
        with self._lock:
            self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: Queue[bytes]) -> None:
        with self._lock:
            self._subscribers.discard(queue)

    def publish(self, event: str, **fields: object) -> None:
        """Publish without blocking or raising into the modem/TNC path."""
        if not event:
            return
        with self._lock:
            self._sequence += 1
            sequence = self._sequence
            subscribers = tuple(self._subscribers)
        record: dict[str, object] = {
            "schema": SCHEMA_VERSION,
            "seq": sequence,
            "ts": self._timestamp_factory(),
            "event": event,
        }
        record.update(fields)
        try:
            payload = (json.dumps(record, separators=(",", ":"), ensure_ascii=True) + "\n").encode("utf-8")
        except BaseException:
            with self._lock:
                self._encoding_errors += 1
            return
        drops = 0
        for queue in subscribers:
            try:
                queue.put_nowait(payload)
            except Full:
                drops += 1
        with self._lock:
            self._published += 1
            self._drops += drops


class ThreadingMonitorServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address: tuple[str, int], hub: MonitorEventHub) -> None:
        self.hub = hub
        super().__init__(address, MonitorHandler)


class MonitorHandler(socketserver.BaseRequestHandler):
    server: ThreadingMonitorServer

    def handle(self) -> None:
        queue = self.server.hub.subscribe()
        self.request.settimeout(1.0)
        try:
            while True:
                try:
                    payload = queue.get(timeout=0.5)
                except Empty:
                    continue
                try:
                    self.request.sendall(payload)
                except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
                    return
        finally:
            self.server.hub.unsubscribe(queue)


def start_monitor_server_thread(hub: MonitorEventHub, *, host: str = "127.0.0.1", port: int = 8002):
    if not 0 <= port <= 65535:
        raise ValueError("monitor TCP port must be 0..65535")
    server = ThreadingMonitorServer((host, port), hub)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, name="ywd-tncd-monitor", daemon=True)
    thread.start()
    return server, thread


def stop_monitor_server_thread(server: ThreadingMonitorServer, thread: threading.Thread) -> None:
    server.shutdown()
    server.server_close()
    thread.join(timeout=2.0)
    if thread.is_alive():
        raise RuntimeError("timed out stopping monitor server")


def _info_text(data: bytes) -> str:
    out: list[str] = []
    for value in data:
        if 32 <= value <= 126:
            out.append(chr(value))
        elif value == 13:
            out.append("\\r")
        elif value == 10:
            out.append("\\n")
        elif value == 9:
            out.append("\\t")
        else:
            out.append(f"\\x{value:02X}")
    return "".join(out)


def frame_fields(frame_no_fcs: bytes) -> dict[str, object]:
    frame = bytes(frame_no_fcs)
    fields: dict[str, object] = {"frame_bytes": len(frame), "frame_hex": frame.hex().upper()}
    try:
        parsed = parse_frame(frame, has_fcs=False)
    except (TypeError, ValueError) as exc:
        fields["parse_error"] = str(exc)
        return fields
    info = bytes(parsed["info"])
    fields.update({
        "source": str(parsed["source"]),
        "destination": str(parsed["destination"]),
        "path": [str(item) for item in parsed["path"]],
        "frame_class": str(parsed["frame_class"]),
        "frame_type": str(parsed["frame_type"]),
        "control": int(parsed["control"]),
        "poll_final": bool(parsed["poll_final"]),
        "ns": parsed["ns"], "nr": parsed["nr"], "pid": parsed["pid"],
        "info_text": _info_text(info), "info_hex": info.hex().upper(),
    })
    return fields
