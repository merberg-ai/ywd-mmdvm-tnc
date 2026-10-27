"""FM1 backup catalog and protected backup worker tests. Hardware never accessed."""
from __future__ import annotations

import http.client
import importlib.util
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from ywdfws.inventory import inventory
from ywdfws.broker import fetch

ROOT = Path(__file__).resolve().parents[1]


class CatalogTests(unittest.TestCase):
    def test_reference_inventory_is_not_live_detected(self):
        report = inventory(backup_roots=())
        self.assertFalse(report["live_probe_performed"])
        self.assertEqual(report["running_firmware"]["state"], "not_probed")
        self.assertEqual(report["target"]["mcu"], "STM32F103")
        self.assertEqual(report["target"]["flash_size_bytes"], 131072)
        self.assertFalse(report["capabilities"]["flash"])
        self.assertFalse(report["capabilities"]["backup_browser"])

    def test_capture_verified_and_tampered_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            prof = inventory(backup_roots=())["target"]
            base = Path(tmp) / prof["id"] / "FWS-20261027-123456-a1b2c3d4"
            base.mkdir(parents=True)
            payload = b"FWS" * 43690 + b"12"
            self.assertEqual(len(payload), 131072)
            digest = __import__("hashlib").sha256(payload).hexdigest()
            image = base / "captured-flash.bin"
            image.write_bytes(payload)
            (base / "manifest.json").write_text(json.dumps({
                "schema": "fws-backup-1",
                "target_id": prof["id"],
                "captured_identity": "YWD test",
                "captured_unix": 123456789,
                "flash_base": "0x08000000",
                "flash_size_bytes": 131072,
                "sha256": digest,
                "read_passes": 2,
                "two_pass_byte_identical": True,
                "option_bytes_read_or_written": False,
                "flash_written": False,
                "restore_qualified": False,
            }))
            report = inventory(backup_roots=(Path(tmp),))
            self.assertEqual(report["backup_count"], 1)
            self.assertEqual(report["backups"][0]["integrity"], "verified")
            self.assertEqual(report["backups"][0]["kind"], "captured_firmware")
            self.assertFalse(report["backups"][0]["restore_qualified"])
            image.write_bytes(b"broken" + payload[6:])
            report = inventory(backup_roots=(Path(tmp),))
            self.assertEqual(report["backups"][0]["integrity"], "invalid")
            self.assertFalse(report["backups"][0]["restore_qualified"])

    def test_inaccessible_target_folder_reports_restricted_not_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            target_id = inventory(backup_roots=())["target"]["id"]
            base = Path(tmp) / target_id
            base.mkdir()
            original = Path.iterdir

            def protected_iterdir(path):
                if path == base:
                    raise PermissionError("root-owned backup catalog")
                return original(path)

            with patch.object(Path, "iterdir", protected_iterdir):
                report = inventory(backup_roots=(Path(tmp),))
            self.assertEqual(report["backup_access"], "restricted")
            self.assertEqual(report["backup_count"], 0)
            self.assertFalse(report["live_probe_performed"])

    def test_permission_error_from_exists_is_not_http_503(self):
        with tempfile.TemporaryDirectory() as tmp:
            target_id = inventory(backup_roots=())["target"]["id"]
            base = Path(tmp) / target_id
            base.mkdir()
            original = Path.exists

            def protected_exists(path, *args, **kwargs):
                if path == base:
                    raise PermissionError("EACCES on protected target path")
                return original(path, *args, **kwargs)

            with patch.object(Path, "exists", protected_exists):
                report = inventory(backup_roots=(Path(tmp),))
            self.assertEqual(report["backup_access"], "restricted")
            self.assertEqual(report["target"]["flash_size_bytes"], 131072)

    def test_parent_backup_folder_permission_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = Path.is_dir

            def protected_is_dir(path):
                if path == root:
                    raise PermissionError("EACCES on legacy backup parent")
                return original(path)

            with patch.object(Path, "is_dir", protected_is_dir):
                report = inventory(backup_roots=(root,))
            self.assertEqual(report["backup_access"], "restricted")

    def test_symlink_entry_is_not_followed(self):
        with tempfile.TemporaryDirectory() as tmp:
            prof = inventory(backup_roots=())["target"]
            base = Path(tmp) / prof["id"]
            base.mkdir()
            (base / "unsafe-link").symlink_to(Path(tmp), target_is_directory=True)
            report = inventory(backup_roots=(Path(tmp),))
            self.assertEqual(report["backup_count"], 0)

    def test_cannot_forge_stock_qualified_from_ywd_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            prof = inventory(backup_roots=())["target"]
            folder = Path(tmp) / prof["id"] / "FWS-forged"
            folder.mkdir(parents=True)
            (folder / "captured-flash.bin").write_bytes(b"\x00" * 131072)
            (folder / "manifest.json").write_text(json.dumps({
                "schema": "fws-backup-1", "target_id": prof["id"],
                "flash_base": "0x08000000", "flash_size_bytes": 131072,
                "sha256": __import__("hashlib").sha256(b"\x00" * 131072).hexdigest(),
                "read_passes": 2, "two_pass_byte_identical": True,
                "flash_written": False, "option_bytes_read_or_written": False,
                "restore_qualified": True,
            }))
            report = inventory(backup_roots=(Path(tmp),))
            self.assertEqual(report["backups"][0]["integrity"], "verified")
            self.assertFalse(report["backups"][0]["restore_qualified"])


class BrokerClientTests(unittest.TestCase):
    def test_reject_malformed_broker_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dummy.sock"
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            listener.bind(str(path))
            listener.listen(1)

            def response():
                conn, _ = listener.accept()
                with conn:
                    assert conn.recv(32) == b"INVENTORY\n"
                    conn.sendall(b'{"schema":17,"phase":"FM1"}\n')
                listener.close()

            t = threading.Thread(target=response, daemon=True)
            t.start()
            with self.assertRaises(ValueError):
                fetch(path)
            t.join(timeout=2)


class BackupWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ROOT / "firmware"))
        spec = importlib.util.spec_from_file_location("fws_backup_under_test", ROOT / "firmware/fws_backup.py")
        assert spec and spec.loader
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)

    def test_worker_has_no_write_flash_opcode_or_web_hook(self):
        content = (ROOT / "firmware/fws_backup.py").read_text()
        self.assertIn('"stm32flash", "-b", "115200", "-r"', content)
        self.assertNotIn('"stm32flash", "-b", "115200", "-w"', content)
        self.assertIn("sys.stdin.isatty()", content)
        self.assertIn("restore=True", content)
        self.assertIn('AUTHORIZATION = "READ-HAT-MAIN-FLASH"', content)

    def test_two_reads_generate_valid_nonrestorable_capture(self):
        from ywdtnc.firmware import load_profile
        profile = load_profile(ROOT / "firmware/product-ax25r4.json")
        with tempfile.TemporaryDirectory() as tmp:
            class FakeBoot:
                def __init__(self, **kw): pass
                def enter(self): pass
                def cleanup(self): pass

            def fake_run(args):
                dest = Path(args[args.index("-r") + 1])
                dest.write_bytes(b"\xa7" * 131072)
                return None

            with patch.object(self.mod.qf, "BACKUP_ROOT", Path(tmp)), \
                 patch.object(self.mod.qf, "Bootloader", FakeBoot), \
                 patch.object(self.mod.qf, "run", fake_run):
                d = self.mod.capture_current_firmware(profile, "/dev/ttyAMA0", profile.expected_identity)
                manifest = json.loads((d / "manifest.json").read_text())
                self.assertEqual(manifest["schema"], "fws-backup-1")
                self.assertFalse(manifest["restore_qualified"])
                self.assertEqual(manifest["read_passes"], 2)
                self.assertFalse(manifest["flash_written"])
                self.assertEqual((d / "captured-flash.bin").stat().st_size, 131072)
                self.assertFalse((d / "read-a.bin").exists())
                self.assertFalse((d / "read-b.bin").exists())

    def test_failed_read_always_checks_modem_service_recovery(self):
        from contextlib import contextmanager, nullcontext, ExitStack
        from ywdtnc.firmware import load_profile
        profile = load_profile(ROOT / "firmware/product-ax25r4.json")

        @contextmanager
        def fake_stopped_owners(device, *, restore):
            self.assertTrue(restore)
            yield

        with ExitStack() as stack:
            for owner, attr, value in (
                (self.mod.qf, "require_root", lambda: None),
                (self.mod.qf, "require_tools", lambda: None),
                (self.mod.qf, "load_profile", lambda path: profile),
                (self.mod.qf, "verify_core_pin", lambda expected: None),
                (self.mod.qf, "load_target", lambda target: {}),
                (self.mod.qf, "find_verified_stock_backup", lambda p: Path("/mock/golden")),
                (self.mod.qf, "verify_stock_backup", lambda *a: None),
                (self.mod, "exclusive_operation", lambda: nullcontext()),
                (self.mod.qf, "stopped_known_modem_owners", fake_stopped_owners),
                (self.mod.qf, "probe_identity", lambda *a: profile.expected_identity),
            ):
                stack.enter_context(patch.object(owner, attr, value))
            stack.enter_context(patch.object(self.mod.Path, "exists", return_value=True))
            active = stack.enter_context(patch.object(
                self.mod, "_active_modem_units",
                side_effect=[["ywd-mmdvm-tnc.service"], ["ywd-mmdvm-tnc.service"]]))
            stack.enter_context(patch.object(
                self.mod, "capture_current_firmware",
                side_effect=self.mod.qf.FlashError("simulated read failure")))
            with self.assertRaisesRegex(self.mod.qf.FlashError, "simulated read failure"):
                self.mod.perform_backup(authorized_by_broker=True)
            self.assertEqual(active.call_count, 2)

    def test_mismatching_read_rejected(self):
        from ywdtnc.firmware import load_profile
        profile = load_profile(ROOT / "firmware/product-ax25r4.json")
        with tempfile.TemporaryDirectory() as tmp:
            class FakeBoot:
                def __init__(self, **kw): pass
                def enter(self): pass
                def cleanup(self): pass
            def fake_run(args):
                dest = Path(args[args.index("-r") + 1])
                dest.write_bytes((b"\x11" if dest.name == "read-a.bin" else b"\x22") * 131072)
            with patch.object(self.mod.qf, "BACKUP_ROOT", Path(tmp)), \
                 patch.object(self.mod.qf, "Bootloader", FakeBoot), \
                 patch.object(self.mod.qf, "run", fake_run):
                with self.assertRaisesRegex(self.mod.qf.FlashError, "differ"):
                    self.mod.capture_current_firmware(profile, "/dev/ttyAMA0", profile.expected_identity)
            self.assertFalse(list(Path(tmp).rglob("manifest.json")))


if __name__ == "__main__":
    unittest.main()
