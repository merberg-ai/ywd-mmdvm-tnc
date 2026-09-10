from __future__ import annotations

from pathlib import Path
import unittest

from ywdtnc.config import load_config


ROOT = Path(__file__).resolve().parents[1]


class MonitorProductContractTests(unittest.TestCase):
    def test_new_config_uses_loopback_live_monitor(self) -> None:
        config = load_config(ROOT / "config" / "ywd-mmdvm-tnc.example.toml")
        self.assertTrue(config.monitor.enabled)
        self.assertEqual(config.monitor.listen, "127.0.0.1")
        self.assertEqual(config.monitor.port, 8002)
        self.assertFalse(config.monitor.allow_wildcard_bind)

    def test_packetlogger_contains_no_transmit_api(self) -> None:
        text = (ROOT / "src" / "ywdtnc" / "packetlog.py").read_text(encoding="utf-8")
        for forbidden in (
            ".send(",
            ".sendall(",
            ".sendto(",
            "transmit_selector_burst",
            "KISSMessage(",
        ):
            self.assertNotIn(forbidden, text)
        self.assertIn("socket.create_connection", text)

    def test_low_level_install_never_auto_enables_packetlogger(self) -> None:
        text = (ROOT / "installer" / "install.sh").read_text(encoding="utf-8")
        self.assertIn("ywd-packetlog.service", text)
        self.assertIn("ywd-packetlog", text)
        for line in text.splitlines():
            if "systemctl" in line and "enable" in line:
                self.assertNotIn("packetlog", line.lower())

    def test_guided_setup_migrates_legacy_logger_only_with_monitor_available(self) -> None:
        text = (ROOT / "installer" / "setup.sh").read_text(encoding="utf-8")
        self.assertIn("PACKETLOG_WAS_ENABLED", text)
        self.assertIn("PACKETLOG_WAS_ACTIVE", text)
        self.assertIn("before-monitor.bak", text)
        self.assertIn("127.0.0.1:8002", text)
        self.assertIn(
            'ui_prompt_yes_no packetlog_answer "Enable the passive packet activity logger at boot?" no',
            text,
        )
        self.assertIn(
            "Migrating/restarting ywd-packetlog on the product monitor stream",
            text,
        )

    def test_packetlogger_service_has_only_log_directory_write_access(self) -> None:
        text = (ROOT / "systemd" / "ywd-packetlog.service").read_text(encoding="utf-8")
        self.assertIn(
            "ExecStart=/opt/ywd-mmdvm-tnc/venv/bin/ywd-packetlog",
            text,
        )
        self.assertIn("ProtectSystem=strict", text)
        self.assertIn("ProtectHome=true", text)
        self.assertIn("LogsDirectory=ywd-packetlog", text)
        self.assertIn("Requires=ywd-mmdvm-tnc.service", text)

    def test_documentation_names_all_lifecycle_events(self) -> None:
        text = (ROOT / "docs" / "monitor-events.md").read_text(encoding="utf-8")
        for event in (
            "rx.frame",
            "tx.submitted",
            "tx.queued",
            "tx.channel_clear",
            "tx.dispatched",
            "tx.complete",
            "tx.rejected",
            "tx.timeout",
            "tx.failed",
        ):
            self.assertIn(event, text)


if __name__ == "__main__":
    unittest.main()
