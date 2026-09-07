from __future__ import annotations

import time
import unittest

from ywd1278.kiss.framing import DATA, KISSMessage
from ywd1278.kiss.server import PacketEvent
from ywdtnc.engine import ModemTNCBackend


class _AdmissionStub:
    request_timeout_seconds = 30.0


class BackendPolicyTests(unittest.TestCase):
    def test_modem_protocol_clients_have_no_history_replay(self) -> None:
        backend = ModemTNCBackend(
            _AdmissionStub(),
            monotonic=time.monotonic,
            history_capacity=0,
            subscriber_queue_capacity=4,
            transmit_enabled=False,
        )
        backend.publish(PacketEvent(b"old"))
        history, queue = backend.open_stream()
        try:
            self.assertEqual(history, [])
            self.assertTrue(queue.empty())
        finally:
            backend.close_stream(queue)

    def test_tx_disabled_rejects_data_at_ingress(self) -> None:
        backend = ModemTNCBackend(
            _AdmissionStub(),
            monotonic=time.monotonic,
            history_capacity=0,
            subscriber_queue_capacity=4,
            transmit_enabled=False,
        )
        result = backend.reject_client_message(KISSMessage(port=0, command=DATA, frame=b"abc"))
        self.assertEqual(result.disposition.value, "data-rejected")
        self.assertEqual(backend.control_counters.kiss_data_tx_rejected, 1)


if __name__ == "__main__":
    unittest.main()
