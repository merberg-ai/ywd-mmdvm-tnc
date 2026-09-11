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
        self.assertFalse(config.kiss.allow_wildcard_bind)
        self.assertEqual(config.agw.listen, "127.0.0.1")
        self.assertFalse(config.agw.allow_wildcard_bind)
        self.assertTrue(config.agw_raw_only)
        self.assertTrue(config.monitor.enabled)
        self.assertEqual(config.monitor.listen, "127.0.0.1")
        self.assertEqual(config.monitor.port, 8002)
        self.assertFalse(config.monitor.allow_wildcard_bind)

    def test_pre_monitor_config_remains_valid_and_monitor_disabled(self) -> None:
        text = EXAMPLE.read_text()
        start = text.index("# Passive newline-delimited JSON event stream")
        end = text.index("[firmware]")
        legacy = text[:start] + text[end:]
        config = self._load_text(legacy)
        self.assertFalse(config.monitor.enabled)
        self.assertEqual(config.monitor.listen, "127.0.0.1")
        self.assertEqual(config.monitor.port, 8002)

    def test_private_lan_bind_is_explicitly_allowed(self) -> None:
        text = EXAMPLE.read_text().replace('listen = "127.0.0.1"', 'listen = "192.168.1.50"')
        config = self._load_text(text)
        self.assertEqual(config.kiss.listen, "192.168.1.50")
        self.assertEqual(config.agw.listen, "192.168.1.50")
        self.assertEqual(config.monitor.listen, "192.168.1.50")

    def test_wildcard_bind_is_rejected_without_opt_in(self) -> None:
        text = EXAMPLE.read_text().replace(
            'listen = "127.0.0.1"\nport = 8001',
            'listen = "0.0.0.0"\nport = 8001',
        )
        with self.assertRaises(TNCConfigurationError):
            self._load_text(text)

    def test_kiss_wildcard_bind_is_allowed_with_explicit_opt_in(self) -> None:
        text = EXAMPLE.read_text().replace(
            'listen = "127.0.0.1"\nport = 8001\nallow_wildcard_bind = false',
            'listen = "0.0.0.0"\nport = 8001\nallow_wildcard_bind = true',
        )
        config = self._load_text(text)
        self.assertEqual(config.kiss.listen, "0.0.0.0")
        self.assertTrue(config.kiss.allow_wildcard_bind)
        self.assertEqual(config.agw.listen, "127.0.0.1")
        self.assertFalse(config.tx_enabled)

    def test_monitor_wildcard_bind_requires_explicit_opt_in(self) -> None:
        text = EXAMPLE.read_text().replace(
            'listen = "127.0.0.1"\nport = 8002\nallow_wildcard_bind = false',
            'listen = "0.0.0.0"\nport = 8002\nallow_wildcard_bind = false',
        )
        with self.assertRaises(TNCConfigurationError):
            self._load_text(text)

    def test_public_bind_is_rejected_even_with_wildcard_opt_in(self) -> None:
        text = EXAMPLE.read_text().replace(
            'listen = "127.0.0.1"\nport = 8001\nallow_wildcard_bind = false',
            'listen = "8.8.8.8"\nport = 8001\nallow_wildcard_bind = true',
        )
        with self.assertRaises(TNCConfigurationError):
            self._load_text(text)

    def test_wildcard_opt_in_must_be_boolean(self) -> None:
        text = EXAMPLE.read_text().replace(
            "allow_wildcard_bind = false",
            'allow_wildcard_bind = "yes"',
            1,
        )
        with self.assertRaises(TNCConfigurationError):
            self._load_text(text)

    def test_physically_qualified_packet_tx_profile_is_allowed(self) -> None:
        text = EXAMPLE.read_text().replace("tx_enabled = false", "tx_enabled = true")
        config = self._load_text(text)
        self.assertTrue(config.tx_enabled)
        self.assertEqual(config.frequency_hz, 145_050_000)
        self.assertEqual(config.tx_power, 200)

    def test_aprs_tx_profile_is_allowed(self) -> None:
        text = EXAMPLE.read_text().replace("tx_enabled = false", "tx_enabled = true")
        text = text.replace("frequency_mhz = 145.050", "frequency_mhz = 144.390")
        config = self._load_text(text)
        self.assertTrue(config.tx_enabled)
        self.assertEqual(config.frequency_hz, 144_390_000)
        self.assertEqual(config.tx_power, 200)

    def test_arbitrary_tx_frequency_is_rejected(self) -> None:
        text = EXAMPLE.read_text().replace("tx_enabled = false", "tx_enabled = true")
        text = text.replace("frequency_mhz = 145.050", "frequency_mhz = 146.520")
        with self.assertRaises(TNCConfigurationError):
            self._load_text(text)

    def test_wrong_tx_power_is_rejected(self) -> None:
        text = EXAMPLE.read_text().replace("tx_enabled = false", "tx_enabled = true")
        text = text.replace("tx_power = 200", "tx_power = 199")
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
