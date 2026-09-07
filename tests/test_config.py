from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from ywdtnc.config import TNCConfigurationError, load_config


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "config" / "ywd-mmdvm-tnc.example.toml"


class ConfigTests(unittest.TestCase):
    def _load_text(self, text: str):
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as handle:
            handle.write(text)
            path = Path(handle.name)
        try:
            return load_config(path)
        finally:
            path.unlink(missing_ok=True)

    def test_example_is_safe_and_valid(self) -> None:
        config = load_config(EXAMPLE)
        self.assertFalse(config.tx_enabled)
        self.assertEqual(config.frequency_hz, 145_050_000)
        self.assertEqual(config.kiss.listen, "127.0.0.1")
        self.assertEqual(config.agw.listen, "127.0.0.1")
        self.assertTrue(config.agw_raw_only)

    def test_private_lan_bind_is_explicitly_allowed(self) -> None:
        text = EXAMPLE.read_text().replace('listen = "127.0.0.1"', 'listen = "192.168.1.50"')
        config = self._load_text(text)
        self.assertEqual(config.kiss.listen, "192.168.1.50")
        self.assertEqual(config.agw.listen, "192.168.1.50")

    def test_wildcard_bind_is_rejected(self) -> None:
        text = EXAMPLE.read_text().replace('listen = "127.0.0.1"', 'listen = "0.0.0.0"')
        with self.assertRaises(TNCConfigurationError):
            self._load_text(text)

    def test_unqualified_tx_profile_is_rejected(self) -> None:
        text = EXAMPLE.read_text().replace("tx_enabled = false", "tx_enabled = true")
        text = text.replace("frequency_mhz = 145.050", "frequency_mhz = 144.390")
        with self.assertRaises(TNCConfigurationError):
            self._load_text(text)

    def test_automatic_flash_is_rejected(self) -> None:
        text = EXAMPLE.read_text().replace(
            "allow_automatic_flash = false", "allow_automatic_flash = true"
        )
        with self.assertRaises(TNCConfigurationError):
            self._load_text(text)


if __name__ == "__main__":
    unittest.main()
