from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
from unittest import mock
import unittest

from ywd1278.ax25.codec import Address, build_ui_frame
from ywd1278.kiss.framing import encode

from ywdtnc import rx_gate


class _ReceiveOnlySocket:
    def __init__(self, payload: bytes) -> None:
        self._chunks = [payload]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout

    def recv(self, maximum: int) -> bytes:
        _ = maximum
        return self._chunks.pop(0) if self._chunks else b""


class RXGateTests(unittest.TestCase):
    def test_gate_receives_without_any_socket_send_api(self) -> None:
        frame = build_ui_frame(
            source=Address.parse("KJ6YWD"),
            destination=Address.parse("JIM"),
            info=b"P1 RX ONLY",
            include_fcs=False,
        )
        sock = _ReceiveOnlySocket(encode(frame))
        out = StringIO()
        with mock.patch("socket.create_connection", return_value=sock), mock.patch.object(
            rx_gate.sys, "argv", ["ywd-tnc-rx-gate", "--timeout", "2"]
        ), redirect_stdout(out):
            rc = rx_gate.main()
        text = out.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("YWD_TNC_P1_LIVE_RX=PASS", text)
        self.assertIn("AX25_SOURCE=KJ6YWD", text)
        self.assertIn("AX25_DESTINATION=JIM", text)
        self.assertIn("AX25_INFORMATION=P1 RX ONLY", text)
        self.assertIn("KISS_BYTES_SENT=0", text)
        self.assertIn("TX_REQUESTED=NO", text)
        self.assertIn("PHYSICAL_GATE_RF_DIRECTION=RX_ONLY", text)


if __name__ == "__main__":
    unittest.main()
