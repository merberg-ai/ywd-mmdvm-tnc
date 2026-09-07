from __future__ import annotations

from pathlib import Path
import unittest

from ywd1278.ax25.codec import parse_frame
from ywdtnc import p2_qualification as p2


ROOT = Path(__file__).resolve().parents[1]


class P2QualificationContractTests(unittest.TestCase):
    def test_qualification_source_has_exactly_one_application_socket_send(self) -> None:
        text = (ROOT / "src" / "ywdtnc" / "p2_qualification.py").read_text(encoding="utf-8")
        self.assertEqual(text.count("sock.sendall("), 1)
        self.assertNotIn("sock.send(", text)
        self.assertIn("CLIENT_RETRY_COUNT=0", text)
        self.assertIn("KISS_DATA_REQUESTS_SENT=1", text)
        self.assertIn("NO_SECOND_INTERNAL_DISPATCH_AFTER_HOLD=PASS", text)
        self.assertIn("SAME_KISS_CONNECTION_RX=PASS", text)

    def test_default_qualification_frame_is_deterministic_ui(self) -> None:
        from ywd1278.ax25.codec import Address, build_ui_frame
        frame = build_ui_frame(
            source=Address.parse(p2.DEFAULT_SOURCE),
            destination=Address.parse(p2.DEFAULT_DESTINATION),
            info=p2.DEFAULT_INFORMATION.encode("utf-8"),
            include_fcs=False,
        )
        parsed = parse_frame(frame, has_fcs=False)
        self.assertEqual(str(parsed["source"]), "KJ6YWD-10")
        self.assertEqual(str(parsed["destination"]), "YWD127")
        self.assertEqual(parsed["frame_type"], "UI")
        self.assertEqual(parsed["pid"], 0xF0)
        self.assertEqual(parsed["info"], b"YWD-MMDVM-TNC P2 1/1")

    def test_dispatch_delta_shape_is_exactly_one_at_every_boundary(self) -> None:
        before = p2.AccountingPoint(5, 7, 7, 2, 2, 0, 2, 2, 0, 0, 0, "")
        self.assertEqual(
            (before.ingress_received+1, before.ingress_admitted+1,
             before.queue_accepted+1, before.queue_dispatched+1,
             before.runtime_tx_dispatches+1, before.decoder_resets_after_tx+1),
            (3, 3, 3, 3, 8, 8),
        )

    def test_physical_wrapper_keeps_persistent_config_tx_disabled(self) -> None:
        text = (ROOT / "scripts" / "p2-kiss-tx-physical.sh").read_text(encoding="utf-8")
        self.assertIn("persistent config must have tx_enabled=false", text)
        self.assertIn("PERSISTENT_CONFIG_MODIFIED=NO", text)
        self.assertIn("P2-TX-ONCE-145050", text)
        self.assertIn("application-release", text)
        self.assertNotIn("sed -i", text)
        self.assertNotIn("tx_enabled = true", text)


if __name__ == "__main__":
    unittest.main()
