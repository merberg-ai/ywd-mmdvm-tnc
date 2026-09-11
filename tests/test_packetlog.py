from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from ywdtnc.packetlog import PacketLogWriter, format_event


class PacketLogTests(unittest.TestCase):
    def test_rx_and_tx_human_formats_are_distinct(self) -> None:
        base = {
            "schema": 1,
            "seq": 1,
            "ts": "2026-09-10T13:00:00.000Z",
            "source": "KJ6YWD-11",
            "destination": "BEACON",
            "path": ["YWDNOD"],
            "frame_class": "U",
            "frame_type": "UI",
            "poll_final": False,
            "pid": 0xF0,
            "info_text": "YWDXR TEST",
            "info_hex": b"YWDXR TEST".hex().upper(),
        }
        rx = format_event({**base, "event": "rx.frame"})
        tx = format_event({**base, "event": "tx.complete", "request_id": 7})
        self.assertIn(
            " RX fm KJ6YWD-11 to BEACON via YWDNOD ctl UI- pid=F0 len=10  YWDXR TEST",
            rx,
        )
        self.assertIn(
            " TX COMPLETE #7 fm KJ6YWD-11 to BEACON via YWDNOD ctl UI- pid=F0 len=10",
            tx,
        )

    def test_connected_mode_control_fields_are_human_readable(self) -> None:
        rr = format_event({
            "schema": 1,
            "seq": 2,
            "ts": "2026-09-10T13:00:00.000Z",
            "event": "rx.frame",
            "source": "KE6CHO-5",
            "destination": "KJ6YWD-11",
            "path": [],
            "frame_class": "S",
            "frame_type": "RR",
            "poll_final": False,
            "nr": 6,
            "pid": None,
            "info_text": "",
            "info_hex": "",
        })
        self.assertIn("RX fm KE6CHO-5 to KJ6YWD-11 ctl RR6- len=0", rr)

        iframe = format_event({
            "schema": 1,
            "seq": 3,
            "ts": "2026-09-10T13:00:00.000Z",
            "event": "rx.frame",
            "source": "KJ6YWD-5",
            "destination": "KJ6YWD-11",
            "path": [],
            "frame_class": "I",
            "frame_type": "I",
            "poll_final": True,
            "ns": 3,
            "nr": 5,
            "pid": 0xF0,
            "info_text": "OK",
            "info_hex": "4F4B",
        })
        self.assertIn("ctl I35+ pid=F0 len=2  OK", iframe)

    def test_writer_keeps_human_log_and_lossless_jsonl(self) -> None:
        event = {
            "schema": 1,
            "seq": 42,
            "ts": "2026-09-10T13:00:00.000Z",
            "event": "tx.dispatched",
            "request_id": 3,
            "source": "KJ6YWD-11",
            "destination": "KJ6YWD-5",
            "path": [],
            "frame_class": "U",
            "frame_type": "SABM",
            "poll_final": True,
            "pid": None,
            "info_text": "",
            "info_hex": "",
            "selector_count": 1234,
        }
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            writer = PacketLogWriter(directory)
            human = writer.write(event)
            logs = list(directory.glob("*.log"))
            jsonls = list(directory.glob("*.jsonl"))
            self.assertEqual(len(logs), 1)
            self.assertEqual(len(jsonls), 1)
            self.assertEqual(logs[0].read_text().strip(), human)
            self.assertIn("ctl SABM+", human)
            self.assertEqual(json.loads(jsonls[0].read_text()), event)


if __name__ == "__main__":
    unittest.main()
