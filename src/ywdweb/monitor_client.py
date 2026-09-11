"""Receive-only client for the qualified localhost NDJSON monitor stream."""
from __future__ import annotations

import json
import socket
import threading

from .config import MonitorSourceConfig
from .state import LiveState


class MonitorReader:
    def __init__(self, config: MonitorSourceConfig, state: LiveState) -> None:
        self.config = config
        self.state = state
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._socket: socket.socket | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="ywd-webui-monitor", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        sock = self._socket
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RD)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _run(self) -> None:
        while not self._stop.is_set():
            self.state.set_connecting()
            try:
                with socket.create_connection((self.config.host, self.config.port), timeout=5.0) as sock:
                    self._socket = sock
                    sock.settimeout(None)
                    self.state.set_connected()
                    with sock.makefile("r", encoding="utf-8", errors="replace", newline="\n") as stream:
                        for line in stream:
                            if self._stop.is_set():
                                return
                            if not line.strip():
                                continue
                            try:
                                event = json.loads(line)
                            except json.JSONDecodeError:
                                self.state.count_bad_json()
                                continue
                            if not isinstance(event, dict) or event.get("schema") != 1:
                                self.state.count_bad_schema()
                                continue
                            self.state.publish(event)
                    raise ConnectionError("monitor stream closed")
            except (OSError, ConnectionError):
                if self._stop.is_set():
                    return
                self.state.set_disconnected()
                self._stop.wait(self.config.reconnect_seconds)
            finally:
                self._socket = None
