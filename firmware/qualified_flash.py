#!/usr/bin/env python3
"""Explicit, qualified AX25R4 firmware deployment for YWD-MMDVM-TNC.

This is intentionally separate from installation and service startup. It can
read/write only STM32 main flash through the ROM bootloader; option bytes are
never addressed. A main-flash write requires the exact qualified artifact, a
verified stock rollback backup, an authorization token, and an interactive
final confirmation.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ywdtnc.firmware import (  # noqa: E402
    FirmwareProfileError,
    load_profile,
    sha256_file,
    verify_artifact,
    verify_programmed_readback,
    verify_stock_backup,
)

PROFILE_PATH = ROOT / "firmware" / "product-ax25r4.json"
VENDOR_FW = ROOT / "vendor" / "ywd-1278" / "firmware"
TARGETS = VENDOR_FW / "targets.json"
HAT_CONTROL = VENDOR_FW / "hat_control.py"
PROBE = VENDOR_FW / "probe_hat.py"
BACKUP_ROOT = Path("/var/lib/ywd-mmdvm-tnc/firmware-backups")
LEGACY_BACKUP_ROOT = Path("/var/lib/ywd-1278/firmware-backups")

KNOWN_MODEM_UNITS = (
    "ywd-mmdvm-tnc.service",
    "ywd-1278.service",
    "MMDVMHost.service",
    "mmdvmhost.service",
    "ywd-mmdvmhost.service",
    "ywd-hotspot-mmdvmhost.service",
)


class FlashError(RuntimeError):
    pass


def run(
    args: list[str],
    *,
    check: bool = True,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        text=True,
        capture_output=capture,
        check=check,
    )


def require_root() -> None:
    if os.geteuid() != 0:
        raise FlashError("run with sudo/root")


def require_tools() -> None:
    for name in ("python3", "stm32flash", "pinctrl", "fuser", "systemctl"):
        if shutil.which(name) is None:
            raise FlashError(f"missing required tool: {name}")


def verify_core_pin(expected: str) -> None:
    proc = run(["git", "-c", "safe.directory=*", "-C", str(ROOT / "vendor" / "ywd-1278"), "rev-parse", "HEAD"], capture=True)
    actual = proc.stdout.strip()
    if actual != expected:
        raise FlashError(f"qualified core mismatch: expected {expected}, got {actual or 'UNKNOWN'}")


def load_target(target_id: str) -> dict:
    try:
        data = json.loads(TARGETS.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FlashError(f"cannot load pinned target manifest: {exc}") from exc
    matches = [item for item in data.get("targets", []) if item.get("id") == target_id]
    if len(matches) != 1:
        raise FlashError("qualified hardware target is missing or ambiguous")
    return matches[0]


def uart_busy(device: str) -> bool:
    return subprocess.run(
        ["fuser", device],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0


@contextmanager
def stopped_known_modem_owners(device: str, *, restore: bool):
    active: list[str] = []
    try:
        for unit in KNOWN_MODEM_UNITS:
            state = subprocess.run(
                ["systemctl", "is-active", "--quiet", unit],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            if state.returncode == 0:
                active.append(unit)
                subprocess.run(
                    ["systemctl", "stop", unit],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
        time.sleep(0.25)
        if uart_busy(device):
            details = run(["fuser", "-v", device], check=False, capture=True)
            raise FlashError(
                f"UART still has an unknown owner after stopping known modem services: "
                f"{device}\n{details.stdout}{details.stderr}"
            )
        yield
    finally:
        if restore:
            for unit in active:
                subprocess.run(
                    ["systemctl", "start", unit],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )


def probe_identity(device: str, target_id: str) -> str:
    proc = run(
        [
            sys.executable,
            str(PROBE),
            "--device",
            device,
            "--targets",
            str(TARGETS),
            "--no-application-release",
            "--json",
        ],
        capture=True,
    )
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise FlashError("safe HAT probe did not return JSON") from exc
    if result.get("matched_target_ids") != [target_id]:
        raise FlashError("running HAT identity does not uniquely match the qualified target")
    if result.get("rf_configured") is not False or result.get("flash_written") is not False:
        raise FlashError("safe HAT probe returned unsafe state markers")
    identity = result.get("identity")
    if not isinstance(identity, str) or not identity:
        raise FlashError("running HAT identity is empty")
    return identity


class Bootloader:
    def __init__(self, *, device: str, target_id: str, expected_version: str, expected_device: str):
        self.device = device
        self.target_id = target_id
        self.expected_version = expected_version.lower()
        self.expected_device = expected_device.lower()
        self.active = False

    def enter(self) -> None:
        run(
            [
                sys.executable,
                str(HAT_CONTROL),
                "bootloader-entry",
                "--targets",
                str(TARGETS),
                "--target",
                self.target_id,
            ]
        )
        self.active = True
        time.sleep(0.5)
        info = run(["stm32flash", "-b", "115200", self.device], capture=True)
        text = info.stdout + info.stderr

        def grab(label: str) -> str:
            match = re.search(rf"{label}\s*:\s*(0x[0-9A-Fa-f]+)", text)
            return match.group(1).lower() if match else ""

        version = grab("Version")
        device_id = grab("Device ID")
        if version != self.expected_version or device_id != self.expected_device:
            raise FlashError(
                f"STM32 bootloader identity mismatch: version={version or 'UNKNOWN'} "
                f"device={device_id or 'UNKNOWN'}"
            )
        print(f"STM32_BOOTLOADER_VERSION={version}")
        print(f"STM32_DEVICE_ID={device_id}")
        print("STM32_BOOTLOADER_IDENTITY=PASS")

    def restart_application(self) -> None:
        if not self.active:
            return
        run(
            [
                sys.executable,
                str(HAT_CONTROL),
                "application-restart",
                "--targets",
                str(TARGETS),
                "--target",
                self.target_id,
            ]
        )
        self.active = False
        time.sleep(1.5)

    def cleanup(self) -> None:
        if not self.active:
            return
        subprocess.run(
            [
                sys.executable,
                str(HAT_CONTROL),
                "application-restart",
                "--targets",
                str(TARGETS),
                "--target",
                self.target_id,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        self.active = False
        time.sleep(0.5)


def stock_identity(target: dict, identity: str) -> bool:
    return identity in (target.get("stock_identities") or [])


def accepted_identity(target: dict, identity: str) -> bool:
    exact = target.get("accepted_running_identities") or []
    prefix = str(target.get("ywd1278_identity_prefix") or "")
    return identity in exact or bool(prefix and identity.startswith(prefix))


def make_stock_backup(profile, target: dict, device: str, identity: str) -> Path:
    if not stock_identity(target, identity):
        raise FlashError("protected rollback backup may be captured only from exact allowlisted stock firmware")

    timestamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    directory = BACKUP_ROOT / profile.target_id / timestamp
    directory.mkdir(parents=True, mode=0o700)
    os.chmod(directory, 0o700)

    a = directory / "read-a.bin"
    b = directory / "read-b.bin"
    image = directory / "original-flash.bin"

    boot = Bootloader(
        device=device,
        target_id=profile.target_id,
        expected_version=profile.expected_bootloader_version,
        expected_device=profile.expected_device_id,
    )
    try:
        boot.enter()
        for path in (a, b):
            run(
                [
                    "stm32flash",
                    "-b",
                    "115200",
                    "-r",
                    str(path),
                    "-S",
                    f"{profile.flash_base}:{profile.stock_flash_size_bytes}",
                    device,
                ]
            )
            os.chmod(path, 0o600)
        if a.read_bytes() != b.read_bytes():
            raise FlashError("two independent stock main-flash reads were not byte-identical")
        digest = sha256_file(a)
        if a.stat().st_size != profile.stock_flash_size_bytes:
            raise FlashError("stock backup size mismatch")
        if digest != profile.stock_flash_sha256:
            raise FlashError(
                "stock backup SHA256 does not match the physically qualified golden stock image"
            )
        shutil.copy2(a, image)
        os.chmod(image, 0o600)
        manifest = {
            "schema": 2,
            "product": "YWD-MMDVM-TNC",
            "target_id": profile.target_id,
            "captured_identity": identity,
            "device": device,
            "flash_base": profile.flash_base,
            "flash_size_bytes": profile.stock_flash_size_bytes,
            "sha256": digest,
            "read_passes": 2,
            "two_pass_byte_identical": True,
            "stock_sha256_expected": profile.stock_flash_sha256,
            "stock_sha256_match": True,
            "expected_bootloader_version": profile.expected_bootloader_version,
            "expected_device_id": profile.expected_device_id,
            "captured_unix": int(time.time()),
            "option_bytes_read_or_written": False,
            "flash_written": False,
        }
        (directory / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.chmod(directory / "manifest.json", 0o600)
    finally:
        boot.cleanup()
    a.unlink(missing_ok=True)
    b.unlink(missing_ok=True)
    verify_stock_backup(profile, directory)
    print("YWD_TNC_STOCK_BACKUP=PASS")
    print(f"STOCK_BACKUP_DIR={directory}")
    print(f"STOCK_BACKUP_SHA256={profile.stock_flash_sha256}")
    print("OPTION_BYTES_READ_OR_WRITTEN=NO")
    print("FLASH_WRITTEN=NO")
    return directory


def find_verified_stock_backup(profile) -> Path | None:
    candidates: list[Path] = []
    for root in (BACKUP_ROOT, LEGACY_BACKUP_ROOT):
        base = root / profile.target_id
        if base.is_dir():
            candidates.extend(path for path in base.iterdir() if path.is_dir())
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    for candidate in candidates:
        try:
            verify_stock_backup(profile, candidate)
        except FirmwareProfileError:
            continue
        return candidate
    return None


def readback_product(profile, device: str, output: Path) -> str:
    run(
        [
            "stm32flash",
            "-b",
            "115200",
            "-r",
            str(output),
            "-S",
            f"{profile.flash_base}:{profile.programmed_readback_bytes}",
            device,
        ]
    )
    os.chmod(output, 0o600)
    return verify_programmed_readback(profile, output)


def write_ready_record(
    profile,
    *,
    identity: str,
    backup_dir: Path | None,
    flash_written: bool,
) -> None:
    out = Path(profile.state_record)
    out.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "schema": 1,
        "product": "YWD-MMDVM-TNC",
        "series": "AX25R4",
        "status": "FIRMWARE-READY",
        "target_id": profile.target_id,
        "artifact_sha256": profile.artifact_sha256,
        "programmed_readback_sha256": profile.programmed_readback_sha256,
        "runtime_identity": identity,
        "stock_backup_dir": "" if backup_dir is None else str(backup_dir),
        "stock_backup_verified": backup_dir is not None,
        "flash_written_this_run": flash_written,
        "tx_enabled_during_flash": False,
        "automatic_flash_enabled": False,
        "option_bytes_written": False,
        "service_enabled_by_firmware_tool": False,
        "created_unix": int(time.time()),
    }
    out.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(out, 0o600)
    print(f"FIRMWARE_READY_RECORD={out}")


def main() -> int:
    parser = argparse.ArgumentParser(prog="qualified_flash.py")
    parser.add_argument("mode", choices=("probe", "backup", "flash"))
    parser.add_argument("--device", default="/dev/ttyAMA0")
    parser.add_argument("--firmware", default="")
    parser.add_argument("--stock-backup-dir", default="")
    parser.add_argument("--authorize", default="")
    args = parser.parse_args()

    try:
        require_root()
        require_tools()
        profile = load_profile(PROFILE_PATH)
        verify_core_pin(profile.qualified_core_commit)
        target = load_target(profile.target_id)
        if not Path(args.device).exists():
            raise FlashError(f"modem UART does not exist: {args.device}")

        firmware = (
            Path(args.firmware)
            if args.firmware
            else ROOT / profile.artifact_relative_path
        )

        print("===== YWD-MMDVM-TNC QUALIFIED AX25R4 FIRMWARE TOOL =====")
        print(f"MODE={args.mode}")
        print(f"TARGET_ID={profile.target_id}")
        print("AUTOMATIC_FLASH=NO")
        print("RF_TX_PERMITTED=NO")
        print("OPTION_BYTES_PERMITTED=NO")

        with stopped_known_modem_owners(args.device, restore=args.mode != "flash"):
            identity = probe_identity(args.device, profile.target_id)
            print(f"RUNNING_IDENTITY={identity}")
            if not accepted_identity(target, identity):
                raise FlashError("running firmware identity is outside the allowlisted target lineage")

            if args.mode == "probe":
                print("YWD_TNC_FIRMWARE_PROBE=PASS")
                print("FLASH_WRITTEN=NO")
                print("RF_TRANSMITTED=NO")
                return 0

            if args.mode == "backup":
                make_stock_backup(profile, target, args.device, identity)
                print("YWD_TNC_FIRMWARE_BACKUP=PASS")
                print("RF_TRANSMITTED=NO")
                return 0

            if args.authorize != profile.flash_authorization_token:
                raise FlashError(
                    f"flash mode requires --authorize {profile.flash_authorization_token}"
                )
            verify_artifact(profile, firmware)
            print("YWD_TNC_FIRMWARE_ARTIFACT=PASS")
            print(f"PRODUCT_FIRMWARE={firmware}")
            print(f"PRODUCT_FIRMWARE_SHA256={profile.artifact_sha256}")

            backup_dir: Path | None
            if args.stock_backup_dir:
                backup_dir = Path(args.stock_backup_dir)
                verify_stock_backup(profile, backup_dir)
            elif stock_identity(target, identity):
                backup_dir = make_stock_backup(profile, target, args.device, identity)
            else:
                backup_dir = find_verified_stock_backup(profile)

            flash_written = False
            boot = Bootloader(
                device=args.device,
                target_id=profile.target_id,
                expected_version=profile.expected_bootloader_version,
                expected_device=profile.expected_device_id,
            )
            readback_fd, readback_name = tempfile.mkstemp(prefix="ywd-tnc-readback.", suffix=".bin")
            os.close(readback_fd)
            readback_path = Path(readback_name)
            try:
                if identity == profile.expected_identity:
                    print(
                        "Exact qualified AX25R4 identity already runs; verifying programmed "
                        "bytes without rewriting main flash."
                    )
                    boot.enter()
                    readback_product(profile, args.device, readback_path)
                    boot.restart_application()
                    print("EXISTING_PRODUCT_FIRMWARE_VERIFIED=YES")
                else:
                    if backup_dir is None:
                        raise FlashError(
                            "a verified stock rollback backup is required before any main-flash write"
                        )
                    verify_stock_backup(profile, backup_dir)
                    response = input(
                        f"Type {profile.final_write_confirmation} to write the exact "
                        f"qualified image: "
                    ).strip()
                    if response != profile.final_write_confirmation:
                        raise FlashError("firmware write cancelled")
                    boot.enter()
                    run(
                        [
                            "stm32flash",
                            "-b",
                            "115200",
                            "-w",
                            str(firmware),
                            "-v",
                            args.device,
                        ]
                    )
                    flash_written = True
                    readback_product(profile, args.device, readback_path)
                    boot.restart_application()
            finally:
                boot.cleanup()
                readback_path.unlink(missing_ok=True)

            post_identity = probe_identity(args.device, profile.target_id)
            if post_identity != profile.expected_identity:
                raise FlashError(
                    f"post-operation runtime identity mismatch: {post_identity}"
                )
            write_ready_record(
                profile,
                identity=post_identity,
                backup_dir=backup_dir,
                flash_written=flash_written,
            )
            print("YWD_TNC_QUALIFIED_FIRMWARE_DEPLOY=PASS")
            print(f"FLASH_WRITTEN={'YES' if flash_written else 'NO'}")
            print("PROGRAMMED_READBACK=PASS")
            print("PRODUCT_RUNTIME_IDENTITY_VERIFIED=YES")
            print(f"STOCK_ROLLBACK_VERIFIED={'YES' if backup_dir else 'NO-NOT-REQUIRED-FOR-NO-WRITE'}")
            print("OPTION_BYTES_WRITTEN=NO")
            print("TX_ENABLED_DURING_FLASH=NO")
            print("RF_TRANSMITTED=NO")
            print("SERVICE_STARTED=NO")
            return 0

    except (FlashError, FirmwareProfileError, OSError, subprocess.SubprocessError) as exc:
        print(f"YWD_TNC_FIRMWARE_TOOL=FAIL:{exc}", file=sys.stderr)
        print("OPTION_BYTES_WRITTEN=NO", file=sys.stderr)
        print("RF_TRANSMITTED=NO", file=sys.stderr)
        return 20


if __name__ == "__main__":
    raise SystemExit(main())
