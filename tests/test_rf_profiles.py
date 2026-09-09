from __future__ import annotations

from pathlib import Path
import struct
import tempfile
import unittest

from ywd1278.modem import protocol
from ywdtnc.config import load_config
from ywdtnc.profile_cli import identify_profile, rewrite_radio_settings
from ywdtnc.rf_profiles import build_tx_profile_request


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "config" / "ywd-mmdvm-tnc.example.toml"


class RFProfileTests(unittest.TestCase):
    def _load_text(self, text: str):
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as handle:
            handle.write(text)
            path = Path(handle.name)
        try:
            return load_config(path)
        finally:
            path.unlink(missing_ok=True)

    def test_packet_request_matches_simplex_145050_power_200(self) -> None:
        payload = (
            bytes((0x00,))
            + struct.pack("<I", 145_050_000)
            + struct.pack("<I", 145_050_000)
            + bytes((200,))
        )
        self.assertEqual(
            build_tx_profile_request(145_050_000, 200),
            protocol.build_frame(protocol.SET_FREQ, payload),
        )

    def test_aprs_request_matches_simplex_144390_power_200(self) -> None:
        payload = (
            bytes((0x00,))
            + struct.pack("<I", 144_390_000)
            + struct.pack("<I", 144_390_000)
            + bytes((200,))
        )
        self.assertEqual(
            build_tx_profile_request(144_390_000, 200),
            protocol.build_frame(protocol.SET_FREQ, payload),
        )

    def test_request_builder_rejects_arbitrary_profile(self) -> None:
        with self.assertRaises(ValueError):
            build_tx_profile_request(146_520_000, 200)
        with self.assertRaises(ValueError):
            build_tx_profile_request(144_390_000, 199)

    def test_switcher_changes_only_radio_profile_values_for_aprs(self) -> None:
        original = EXAMPLE.read_text(encoding="utf-8")
        changed = rewrite_radio_settings(
            original,
            frequency_hz=144_390_000,
            tx_power=200,
            tx_enabled=True,
        )
        config = self._load_text(changed)
        self.assertEqual(config.frequency_hz, 144_390_000)
        self.assertEqual(config.tx_power, 200)
        self.assertTrue(config.tx_enabled)
        self.assertEqual(config.kiss.listen, "127.0.0.1")
        self.assertIn('required_identity = "MMDVM_HS_Hat-YWD-1278-AX25R4', changed)
        self.assertEqual(identify_profile(config), "aprs")

    def test_switcher_can_disable_tx_without_retuning(self) -> None:
        original = EXAMPLE.read_text(encoding="utf-8")
        aprs = rewrite_radio_settings(
            original,
            frequency_hz=144_390_000,
            tx_power=200,
            tx_enabled=True,
        )
        rx_only = rewrite_radio_settings(
            aprs,
            frequency_hz=144_390_000,
            tx_power=200,
            tx_enabled=False,
        )
        config = self._load_text(rx_only)
        self.assertEqual(config.frequency_hz, 144_390_000)
        self.assertFalse(config.tx_enabled)
        self.assertEqual(identify_profile(config), "rx-only")


if __name__ == "__main__":
    unittest.main()
