from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from ywdtnc.firmware import FirmwareProfileError, load_profile, verify_artifact


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "firmware" / "product-ax25r4.json"


class FirmwareProfileTests(unittest.TestCase):
    def test_exact_qualified_profile(self) -> None:
        profile = load_profile(PROFILE)
        self.assertEqual(profile.product, "YWD-MMDVM-TNC")
        self.assertEqual(profile.series, "AX25R4")
        self.assertEqual(profile.qualified_core_commit, "c28c46c3478d7931af611923c92cd8f692a00858")
        self.assertEqual(profile.target_id, "mmdvm-hs-hat-stm32f103-simplex-14.7456-adf7021")
        self.assertEqual(profile.artifact_size_bytes, 59892)
        self.assertEqual(
            profile.artifact_sha256,
            "b06fcbf0baa36e865198091cee27c66e1624ef08117ee685253a7a5613c7c616",
        )
        self.assertEqual(profile.stock_flash_size_bytes, 131072)
        self.assertEqual(
            profile.stock_flash_sha256,
            "4981b35b2d50ada0b09322d9de19dd58a0cbd49eb005693499d1acae92f9d684",
        )
        self.assertEqual(profile.expected_bootloader_version, "0x22")
        self.assertEqual(profile.expected_device_id, "0x0410")
        self.assertEqual(profile.flash_authorization_token, "FLASH-QUALIFIED-AX25R4")
        self.assertEqual(profile.final_write_confirmation, "WRITE-FIRMWARE-NOW")

    def test_automatic_flash_cannot_be_enabled_in_profile(self) -> None:
        data = json.loads(PROFILE.read_text(encoding="utf-8"))
        data["safety"]["automatic_flash_enabled"] = True
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "unsafe.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(FirmwareProfileError):
                load_profile(path)

    def test_missing_artifact_fails_closed(self) -> None:
        profile = load_profile(PROFILE)
        with self.assertRaises(FirmwareProfileError):
            verify_artifact(profile, ROOT / "does-not-exist.bin")


if __name__ == "__main__":
    unittest.main()
