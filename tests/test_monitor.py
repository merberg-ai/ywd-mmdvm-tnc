from __future__ import annotations

import json
import socket
import time
import unittest

from ywd1278.ax25 import Address, build_ui_frame
from ywdtnc.monitor import MonitorEventHub, frame_fields, start_monitor_server_thread, stop_monitor_server_thread


class MonitorTests(unittest.TestCase):
    def test_frame_fields_decode_common_ui_frame(self) -> None:
        frame = build_ui_frame(
            source=Address("KJ6YWD", 11),
            destination=Address("BEACON"),
            path=(Address("KRDG", flag=True), Address("WOODY")),
            info=b"YWDXR TEST",
            include_fcs=False,
        )
        fields = frame_fields(frame)
        self.assertEqual(fields["source"], "KJ6YWD-11")
        self.assertEqual(fields["destination"], "BEACON")
        self.assertEqual(fields["path"], ["KRDG*", "WOODY"])
        self.assertEqual(fields["frame_type"], "UI")
        self.assertEqual(fields["info_text"], "YWDXR TEST")

    def test_monitor_is_live_only_ndjson_and_read_only(self) -> None:
        hub = MonitorEventHub(timestamp_factory=lambda: "2026-09-10T13:00:00.000Z")
        hub.publish("old.event", value=1)
        server, thread = start_monitor_server_thread(hub, host="127.0.0.1", port=0)
        try:
            host, port = server.server_address[:2]
            with socket.create_connection((host, port), timeout=1.0) as client:
                deadline = time.monotonic() + 1.0
                while hub.snapshot.subscribers != 1 and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertEqual(hub.snapshot.subscribers, 1)
                # Client input is never interpreted as a command or transmit path.
                client.sendall(b"this input has no monitor protocol meaning\n")
                hub.publish("rx.frame", source="KJ6YWD-11", destination="BEACON")
                client.settimeout(1.0)
                payload = client.recv(4096)
            record = json.loads(payload.decode("utf-8").strip())
            self.assertEqual(record["schema"], 1)
            self.assertEqual(record["event"], "rx.frame")
            self.assertEqual(record["source"], "KJ6YWD-11")
            self.assertNotIn("old.event", payload.decode("utf-8"))
        finally:
            stop_monitor_server_thread(server, thread)

    def test_slow_subscriber_drops_do_not_block_publish(self) -> None:
        hub = MonitorEventHub(queue_capacity=1, timestamp_factory=lambda: "T")
        queue = hub.subscribe()
        hub.publish("one")
        hub.publish("two")
        self.assertEqual(queue.qsize(), 1)
        self.assertEqual(hub.snapshot.subscriber_drops, 1)


if __name__ == "__main__":
    unittest.main()
