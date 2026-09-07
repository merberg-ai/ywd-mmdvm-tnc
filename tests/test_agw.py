from __future__ import annotations

import socket
import time
import unittest

from ywd1278.kiss.server import PacketEvent, RXOnlyBackend
from ywdtnc.agw.framing import (
    AGWStreamDecoder,
    HEADER_BYTES,
    RAW_FRAME,
    RAW_MONITOR,
    encode_frame,
)
from ywdtnc.agw.server import start_agw_server_thread, stop_agw_server_thread


class AGWFramingTests(unittest.TestCase):
    def test_header_is_36_bytes(self) -> None:
        self.assertEqual(HEADER_BYTES, 36)

    def test_split_and_coalesced_stream(self) -> None:
        first = encode_frame(RAW_MONITOR)
        second = encode_frame(RAW_FRAME, b"\x00abc")
        decoder = AGWStreamDecoder()
        self.assertEqual(decoder.feed(first[:7]), [])
        frames = decoder.feed(first[7:] + second)
        self.assertEqual(len(frames), 2)
        self.assertEqual(frames[0].header.data_kind, RAW_MONITOR)
        self.assertEqual(frames[1].header.data_kind, RAW_FRAME)
        self.assertEqual(frames[1].data, b"\x00abc")


class AGWServerTests(unittest.TestCase):
    def test_raw_monitor_is_live_only_and_raw_send_hits_shared_backend(self) -> None:
        backend = RXOnlyBackend(history_capacity=8, subscriber_queue_capacity=8)
        backend.publish(PacketEvent(b"OLD"))
        server, thread = start_agw_server_thread(backend, host="127.0.0.1", port=0)
        host, port = server.server_address[:2]
        client = socket.create_connection((host, port), timeout=1.0)
        client.settimeout(1.0)
        try:
            client.sendall(encode_frame(RAW_MONITOR))
            time.sleep(0.05)
            backend.publish(PacketEvent(b"NEW"))
            data = client.recv(4096)
            frames = AGWStreamDecoder().feed(data)
            self.assertEqual(len(frames), 1)
            self.assertEqual(frames[0].header.data_kind, RAW_FRAME)
            self.assertEqual(frames[0].data, b"\x00NEW")

            client.sendall(encode_frame(RAW_FRAME, b"\x00abc"))
            deadline = time.monotonic() + 1.0
            while backend.snapshot.tx_rejected < 1 and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(backend.snapshot.tx_rejected, 1)
        finally:
            client.close()
            stop_agw_server_thread(server, thread)


if __name__ == "__main__":
    unittest.main()
