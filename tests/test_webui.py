from __future__ import annotations

import http.client
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

from ywdweb.app import WebUIApplication, create_server
from ywdweb.config import (
    HistoryConfig,
    MonitorSourceConfig,
    WebConfig,
    WebUIConfig,
    WebUIConfigurationError,
    validate_config,
)
from ywdweb.history import HistoryStore, valid_date
from ywdweb.monitor_client import MonitorReader
from ywdweb.state import LiveState


class ConfigTests(unittest.TestCase):
    def test_safe_defaults(self) -> None:
        cfg = validate_config(WebUIConfig())
        self.assertEqual(cfg.web.listen, "127.0.0.1")
        self.assertEqual(cfg.monitor.host, "127.0.0.1")
        self.assertEqual(cfg.monitor.port, 8002)

    def test_wildcard_requires_explicit_opt_in(self) -> None:
        with self.assertRaises(WebUIConfigurationError):
            validate_config(WebUIConfig(web=WebConfig(listen="0.0.0.0", port=8088, allow_wildcard_bind=False)))
        validate_config(WebUIConfig(web=WebConfig(listen="0.0.0.0", port=8088, allow_wildcard_bind=True)))

    def test_monitor_source_must_remain_loopback(self) -> None:
        with self.assertRaises(WebUIConfigurationError):
            validate_config(WebUIConfig(monitor=MonitorSourceConfig(host="192.168.1.11", port=8002)))


class StateTests(unittest.TestCase):
    def test_slow_browser_drops_without_blocking_publish(self) -> None:
        state = LiveState(recent_capacity=16, sse_queue_capacity=8)
        state.set_connected()
        queue = state.subscribe()
        started = time.monotonic()
        for seq in range(100):
            state.publish({"schema": 1, "seq": seq, "ts": "2026-09-10T00:00:00Z", "event": "rx.frame"})
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertGreater(state.snapshot.sse_drops, 0)
        self.assertEqual(state.snapshot.recent_events, 16)
        state.unsubscribe(queue)

    def test_generation_changes_on_reconnect(self) -> None:
        state = LiveState()
        self.assertEqual(state.set_connected(), 1)
        state.set_disconnected()
        self.assertEqual(state.set_connected(), 2)
        self.assertEqual(state.snapshot.reconnects, 1)


class MonitorReaderTests(unittest.TestCase):
    def test_reads_schema1_without_writing_to_source(self) -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        host, port = listener.getsockname()
        accepted = threading.Event()
        source_done = threading.Event()

        def source() -> None:
            conn, _ = listener.accept()
            accepted.set()
            conn.settimeout(0.25)
            conn.sendall(b'{"schema":1,"seq":9,"ts":"2026-09-10T00:00:00Z","event":"rx.frame","source":"RDG"}\n')
            try:
                data = conn.recv(1)
                self.assertEqual(data, b"")
            except socket.timeout:
                pass
            finally:
                source_done.set()
                conn.close()
                listener.close()

        thread = threading.Thread(target=source, daemon=True)
        thread.start()
        state = LiveState()
        reader = MonitorReader(MonitorSourceConfig(host=host, port=port, reconnect_seconds=0.05), state)
        reader.start()
        self.assertTrue(accepted.wait(1.0))
        deadline = time.monotonic() + 1.0
        while state.snapshot.events_received < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(state.snapshot.events_received, 1)
        self.assertEqual(state.recent()[0]["source"], "RDG")
        reader.stop()
        self.assertTrue(source_done.wait(1.0))


class HistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        events = [
            {"schema": 1, "seq": 1, "ts": "2026-09-10T20:00:00Z", "event": "rx.frame", "source": "RDG", "destination": "KJ6YWD-11", "frame_type": "UI", "info_text": "hello"},
            {"schema": 1, "seq": 2, "ts": "2026-09-10T20:00:01Z", "event": "tx.queued", "request_id": 7, "source": "KJ6YWD-11", "destination": "RDG", "frame_type": "I"},
            {"schema": 1, "seq": 3, "ts": "2026-09-10T20:00:02Z", "event": "tx.channel_clear", "request_id": 7, "source": "KJ6YWD-11", "destination": "RDG", "raw_rssi": 110},
            {"schema": 1, "seq": 4, "ts": "2026-09-10T20:00:03Z", "event": "tx.complete", "request_id": 7, "source": "KJ6YWD-11", "destination": "RDG", "frame_type": "I"},
        ]
        with (self.root / "2026-09-10.jsonl").open("w", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(event) + "\n")
        (self.root / "2026-09-10.log").write_text("one\ntwo\nthree\n", encoding="utf-8")
        self.store = HistoryStore(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_dates_and_filters(self) -> None:
        self.assertEqual(self.store.dates(), ["2026-09-10"])
        self.assertEqual(len(self.store.events("2026-09-10", kind="tx.*")), 3)
        self.assertEqual(len(self.store.events("2026-09-10", station="rdg")), 4)
        self.assertEqual(len(self.store.events("2026-09-10", query="hello")), 1)

    def test_stats(self) -> None:
        stats = self.store.stats("2026-09-10")
        self.assertEqual(stats["rx_frames"], 1)
        self.assertEqual(stats["tx_complete"], 1)
        self.assertEqual(stats["rssi_average"], 110.0)

    def test_console_tail(self) -> None:
        self.assertEqual(self.store.console("2026-09-10", lines=2), ["two", "three"])

    def test_date_traversal_rejected(self) -> None:
        with self.assertRaises(ValueError):
            valid_date("../../etc/passwd")


class HTTPTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        cfg = WebUIConfig(
            web=WebConfig(listen="127.0.0.1", port=0),
            history=HistoryConfig(directory=Path(self.temp.name)),
        )
        self.app = WebUIApplication(cfg)
        self.server, _ = create_server(cfg, application=self.app)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.host, self.port = self.server.server_address[:2]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def get_json(self, path: str) -> dict:
        with urllib.request.urlopen(f"http://{self.host}:{self.port}{path}", timeout=2) as response:
            self.assertEqual(response.getheader("X-Frame-Options"), "DENY")
            return json.loads(response.read())

    def test_status_is_read_only(self) -> None:
        status = self.get_json("/api/v1/status")
        self.assertTrue(status["read_only"])
        self.assertEqual(status["schema"], 1)

    def test_recent_api(self) -> None:
        self.app.state.set_connected()
        self.app.state.publish({"schema": 1, "seq": 1, "ts": "2026-09-10T00:00:00Z", "event": "rx.frame"})
        recent = self.get_json("/api/v1/recent")
        self.assertEqual(recent["events"][0]["event"], "rx.frame")
        self.assertEqual(recent["events"][0]["_web_generation"], 1)

    def test_sse_delivers_published_event(self) -> None:
        conn = http.client.HTTPConnection(self.host, self.port, timeout=2)
        conn.request("GET", "/api/v1/stream")
        response = conn.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("Content-Type"), "text/event-stream; charset=utf-8")
        self.assertEqual(response.fp.readline().decode().strip(), ": ywd-webui passive stream")
        response.fp.readline()
        self.app.state.set_connected()
        self.app.state.publish({"schema": 1, "seq": 22, "ts": "2026-09-10T00:00:00Z", "event": "rx.frame"})
        lines = [response.fp.readline().decode().strip() for _ in range(4)]
        self.assertIn("id: 1:22", lines)
        self.assertIn("event: packet", lines)
        self.assertTrue(any(line.startswith("data: ") and '"seq":22' in line for line in lines))
        conn.close()

    def test_post_is_not_implemented(self) -> None:
        request = urllib.request.Request(f"http://{self.host}:{self.port}/api/v1/status", data=b"x", method="POST")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=2)
        self.assertEqual(caught.exception.code, 501)


if __name__ == "__main__":
    unittest.main()
