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
            "frame_type": "UI",
            "pid": 0xF0,
            "info_text": "YWDXR TEST",
        }
        rx = format_event({**base, "event": "rx.frame"})
        tx = format_event({**base, "event": "tx.complete", "request_id": 7})
        self.assertIn(" RX KJ6YWD-11>BEACON via YWDNOD UI pid=F0 YWDXR TEST", rx)
        self.assertIn(" TX COMPLETE #7 KJ6YWD-11>BEACON via YWDNOD", tx)

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
            self.assertEqual(json.loads(jsonls[0].read_text()), event)


if __name__ == "__main__":
    unittest.main()
