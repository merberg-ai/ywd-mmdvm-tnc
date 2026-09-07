from __future__ import annotations

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


class P1ToolingContractTests(unittest.TestCase):
    def test_low_level_installer_and_bootstrap_do_not_flash_or_start_service(self) -> None:
        """Preserve the original P1 safety boundary after public-UX polish.

        The low-level bootstrap/install scripts may prepare dependencies, copy
        files and register the unit, but they must not deploy firmware or start
        RF. The separate guided setup layer is the explicit operator-controlled
        orchestration path that may invoke those already-qualified operations.
        """

        for relative in ("installer/install.sh", "installer/bootstrap.sh"):
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertNotIn("qualified_flash.py", text)
            self.assertNotIn("firmware/flash.sh", text)
            self.assertIsNone(
                re.search(r"\bsystemctl\s+(?:start|restart|enable)\b", text),
                msg=f"{relative} must not start/enable the modem service",
            )

        install = (ROOT / "installer/install.sh").read_text(encoding="utf-8")
        self.assertNotIn("stm32flash", install)
        self.assertIn("systemctl daemon-reload", install)

    def test_guided_setup_is_the_explicit_deployment_orchestrator(self) -> None:
        setup = (ROOT / "installer/setup.sh").read_text(encoding="utf-8")
        self.assertIn('ui_prompt_yes_no tx_answer "Enable RF transmit?" no', setup)
        self.assertIn('firmware/flash.sh" flash --authorize FLASH-QUALIFIED-AX25R4', setup)
        self.assertIn("WRITE-FIRMWARE-NOW", setup)
        self.assertIn('ui_prompt_yes_no firmware_answer', setup)
        self.assertIn('systemctl enable "$SERVICE"', setup)
        self.assertIn('systemctl restart "$SERVICE"', setup)

    def test_build_delegates_to_exact_frozen_builder(self) -> None:
        text = (ROOT / "firmware" / "build.sh").read_text(encoding="utf-8")
        self.assertIn("c28c46c3478d7931af611923c92cd8f692a00858", text)
        self.assertIn("build-packet-rssi-ywd1278.py", text)
        self.assertIn("FLASH_WRITTEN=NO", text)
        self.assertIn("RF_TRANSMITTED=NO", text)

    def test_rx_gate_has_no_transmit_socket_calls(self) -> None:
        text = (ROOT / "src" / "ywdtnc" / "rx_gate.py").read_text(encoding="utf-8")
        self.assertNotIn(".send(", text)
        self.assertNotIn(".sendall(", text)
        self.assertIn("KISS_BYTES_SENT=0", text)
        self.assertIn("TX_REQUESTED=NO", text)

    def test_physical_gate_waits_for_kiss_listener_before_operator_prompt(self) -> None:
        physical = (ROOT / "scripts" / "p1-rx-physical.sh").read_text(encoding="utf-8")
        gate = (ROOT / "src" / "ywdtnc" / "rx_gate.py").read_text(encoding="utf-8")
        self.assertIn("--connect-timeout", physical)
        self.assertNotIn("Transmit ONE normal 1200-baud AX.25 packet", physical)
        self.assertIn("KISS_CONNECTED=YES", gate)
        self.assertIn("Transmit ONE normal 1200-baud AX.25 packet", gate)

    def test_flasher_never_invokes_option_byte_programming(self) -> None:
        text = (ROOT / "firmware" / "qualified_flash.py").read_text(encoding="utf-8")
        self.assertNotIn('"--option-bytes"', text)
        self.assertNotIn("'--option-bytes'", text)
        self.assertIn("OPTION_BYTES_PERMITTED=NO", text)
        self.assertIn("OPTION_BYTES_WRITTEN=NO", text)


if __name__ == "__main__":
    unittest.main()
