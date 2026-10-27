#!/usr/bin/env python3
"""FM1 operator-only two-pass backup. NEVER writes firmware or option bytes.

Not reachable from the browser; must be invoked as root from an interactive
local maintenance shell. It temporarily stops UART-owning modem services.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time

import qualified_flash as qf


AUTHORIZATION = "READ-HAT-MAIN-FLASH"
LOCK_PATH = Path("/run/lock/ywd-fws-backup.lock")


@contextmanager
def exclusive_operation():
    # Root-only worker. An already-running worker must not overlap.
    fd = os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    except BlockingIOError as exc:
        raise qf.FlashError("another FWS firmware maintenance job is already running") from exc
    finally:
        os.close(fd)


def read_interactive_confirmation() -> None:
    if not sys.stdin.isatty():
        raise qf.FlashError("backup requires an interactive TTY; web/process requests cannot authorize it")
    print("Maintenance will temporarily stop modem UART-owner services and reset the HAT.")
    print("Only STM32 main flash will be READ. There will be NO firmware/option-byte writes.")
    print("A copy of any YWD firmware is a capture, not a stock-qualified rollback image.")
    answer = input(f"Type {AUTHORIZATION} to proceed: ").strip()
    if answer != AUTHORIZATION:
        raise qf.FlashError("backup cancelled by operator")


def capture_current_firmware(profile, device: str, identity: str, progress=None) -> Path:
    """Create a verified but NOT restoration-qualified two-pass YWD image."""
    timestamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    directory = qf.BACKUP_ROOT / profile.target_id / f"FWS-{timestamp}-{secrets.token_hex(4)}"
    directory.mkdir(parents=True, mode=0o700)
    a, b = (directory / "read-a.bin", directory / "read-b.bin")
    image = directory / "captured-flash.bin"
    boot = qf.Bootloader(device=device, target_id=profile.target_id,
                         expected_version=profile.expected_bootloader_version,
                         expected_device=profile.expected_device_id)
    try:
        boot.enter()
        for idx, dest in enumerate((a, b)):
            if progress:
                progress(f"Reading 128 KiB main flash: pass {idx+1} of 2", "read_a" if idx == 0 else "read_b")
            qf.run(["stm32flash", "-b", "115200", "-r", str(dest), "-S",
                    f"{profile.flash_base}:{profile.stock_flash_size_bytes}", device])
            os.chmod(dest, 0o600)
            if dest.stat().st_size != profile.stock_flash_size_bytes:
                raise qf.FlashError("full main-flash read size mismatch")
        if a.read_bytes() != b.read_bytes():
            raise qf.FlashError("independent main-flash reads differ; refusing to save backup")
        if progress:
            progress("Both flash reads match; calculating SHA-256 and saving backup", "verify")
        digest = qf.sha256_file(a)
        os.replace(a, image)
        os.chmod(image, 0o600)
        manifest = {
            "schema": "fws-backup-1",
            "product": "YWD-MMDVM-TNC",
            "target_id": profile.target_id,
            "captured_identity": identity,
            "device": device,
            "flash_base": profile.flash_base,
            "flash_size_bytes": profile.stock_flash_size_bytes,
            "sha256": digest,
            "read_passes": 2,
            "two_pass_byte_identical": True,
            "captured_unix": int(time.time()),
            "stock_sha256_match": digest == profile.stock_flash_sha256,
            "restore_qualified": False,
            "option_bytes_read_or_written": False,
            "flash_written": False,
        }
        path = directory / "manifest.json"
        path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(path, 0o600)
        if progress:
            progress("Captured firmware integrity verified and manifest saved", "verify")
        print("FWS_BACKUP_TWO_PASS_VERIFIED=PASS")
        print("FWS_BACKUP_KIND=CAPTURED_FIRMWARE")
        print("FWS_RESTORE_QUALIFIED=NO")
        print(f"FWS_BACKUP_SHA256={digest}")
        print(f"FWS_BACKUP_DIRECTORY={directory}")
        return directory
    finally:
        boot.cleanup()
        a.unlink(missing_ok=True)
        b.unlink(missing_ok=True)



def _active_modem_units() -> list[str]:
    return [unit for unit in qf.KNOWN_MODEM_UNITS
            if subprocess.run(["systemctl", "is-active", "--quiet", unit],
                              stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, check=False).returncode == 0]


def perform_backup(*, progress=None, authorized_by_broker: bool = False) -> dict:
    """One qualified dual-read maintenance operation.

    Browser mode is permitted ONLY via the root maintenance broker which
    separately enforces a one-time root-TTY-issued approval code.
    The direct CLI remains TTY + typed-confirmation only.
    """
    device = "/dev/ttyAMA0"
    qf.require_root()
    qf.require_tools()
    profile = qf.load_profile(qf.PROFILE_PATH)
    qf.verify_core_pin(profile.qualified_core_commit)
    target = qf.load_target(profile.target_id)
    if not authorized_by_broker:
        read_interactive_confirmation()
    if not Path(device).exists():
        raise qf.FlashError(f"qualified UART does not exist: {device}")
    if progress:
        progress("Verified exact qualified target and read-only backup plan", "preflight")

    with exclusive_operation():
        # Preserve and validate the existing stock rollback before touching
        # a running YWD application. No new backup may overwrite the golden.
        golden = qf.find_verified_stock_backup(profile)
        if golden is None:
            raise qf.FlashError("no verified golden stock backup exists; refusing maintenance")
        qf.verify_stock_backup(profile, golden)
        if progress:
            progress("Existing golden stock rollback verified; preparing modem service pause", "service_stop")

        original_active = _active_modem_units()
        operation_error: Exception | None = None
        try:
            with qf.stopped_known_modem_owners(device, restore=True):
                if progress:
                    progress("Modem UART released; probing qualified running identity", "probe")
                identity = qf.probe_identity(device, profile.target_id)
                if identity == profile.expected_identity:
                    captured = capture_current_firmware(profile, device, identity, progress=progress)
                    digest = qf.sha256_file(captured / "captured-flash.bin")
                    kind = "captured_firmware"
                elif qf.stock_identity(target, identity):
                    if progress:
                        progress("Reading exact allowlisted stock firmware using qualified backup path", "read_a")
                    captured = qf.make_stock_backup(profile, target, device, identity)
                    digest = qf.verify_stock_backup(profile, captured)
                    kind = "golden_stock"
                    if progress:
                        progress("Qualified stock backup passed two-pass hash verification", "verify")
                else:
                    raise qf.FlashError("firmware identity not exact qualified YWD or allowlisted stock")
        except Exception as exc:
            operation_error = exc
        finally:
            if progress:
                progress("Checking restoration of previously active modem services", "recovery")
            # Check even after a failed read or unknown identity. The
            # qualified context manager already attempted service restart.
            not_running = [unit for unit in original_active if unit not in _active_modem_units()]
            if not_running:
                raise qf.FlashError("modem service recovery failed: " + ", ".join(not_running)) from operation_error
        if operation_error is not None:
            raise operation_error
        if progress:
            progress("Previously active modem services restored; two-pass backup complete", "recovery")
        print("FWS_BACKUP=PASS")
        print("FWS_FLASH_WRITTEN=NO")
        print("FWS_OPTION_BYTES_WRITTEN=NO")
        print("FWS_RF_COMMAND_SENT_BY_WORKER=NO")
        return {"verified": True, "kind": kind, "sha256": digest}


def main() -> int:
    parser = argparse.ArgumentParser(description="FM1 two-pass firmware backup (root/TTY only)")
    parser.add_argument("operation", choices=("backup",))
    parser.parse_args()
    try:
        perform_backup(authorized_by_broker=False)
        return 0
    except (qf.FlashError, qf.FirmwareProfileError, OSError, ValueError,
            subprocess.SubprocessError) as exc:
        print(f"FWS_BACKUP_ERROR={exc}", file=sys.stderr)
        return 20


if __name__ == "__main__":
    raise SystemExit(main())
