"""Qualified firmware profile and artifact verification for YWD-MMDVM-TNC.

This module is intentionally hardware-inert. It never opens the modem UART,
touches GPIO, invokes a programmer, changes systemd state, or transmits RF.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

from . import (
    PRODUCT_TARGET,
    QUALIFIED_CORE_COMMIT,
    QUALIFIED_CORE_TREE,
    QUALIFIED_FIRMWARE_IDENTITY,
)


class FirmwareProfileError(ValueError):
    pass


@dataclass(frozen=True)
class FirmwareProfile:
    path: Path
    product: str
    series: str
    qualified_core_commit: str
    qualified_core_tree: str
    target_id: str
    expected_identity: str
    vendor_build_script: str
    artifact_relative_path: str
    artifact_size_bytes: int
    artifact_sha256: str
    flash_base: str
    programmed_readback_bytes: int
    programmed_readback_sha256: str
    stock_flash_size_bytes: int
    stock_flash_sha256: str
    expected_bootloader_version: str
    expected_device_id: str
    flash_authorization_token: str
    final_write_confirmation: str
    state_record: str


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FirmwareProfileError(f"cannot load firmware profile {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise FirmwareProfileError("firmware profile root must be an object")
    return value


def _required_str(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise FirmwareProfileError(f"profile field {key} must be a non-empty string")
    return value


def _required_int(data: dict[str, Any], key: str) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise FirmwareProfileError(f"profile field {key} must be a positive integer")
    return int(value)


def load_profile(path: str | Path) -> FirmwareProfile:
    p = Path(path)
    data = _load_json(p)
    if data.get("schema") != 1:
        raise FirmwareProfileError("unsupported firmware profile schema")
    if data.get("product") != "YWD-MMDVM-TNC" or data.get("series") != "AX25R4":
        raise FirmwareProfileError("unexpected firmware product/series")

    safety = data.get("safety")
    if not isinstance(safety, dict):
        raise FirmwareProfileError("missing firmware safety object")
    exact_safety = {
        "automatic_flash_enabled": False,
        "requires_exact_core_pin": True,
        "requires_exact_target": True,
        "requires_exact_artifact_hash": True,
        "requires_verified_stock_backup_before_write": True,
        "requires_programmed_readback": True,
        "requires_exact_runtime_identity": True,
        "option_bytes_permitted": False,
        "tx_must_remain_disabled_during_flash": True,
        "installer_may_flash": False,
        "service_may_auto_flash": False,
    }
    for key, expected in exact_safety.items():
        if safety.get(key) is not expected:
            raise FirmwareProfileError(f"unsafe firmware profile: safety.{key}")

    artifact_sha = _required_str(data, "artifact_sha256").lower()
    readback_sha = _required_str(data, "programmed_readback_sha256").lower()
    stock_sha = _required_str(data, "stock_flash_sha256").lower()
    for label, digest in (
        ("artifact", artifact_sha),
        ("programmed readback", readback_sha),
        ("stock", stock_sha),
    ):
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise FirmwareProfileError(f"invalid {label} SHA256")
    if artifact_sha != readback_sha:
        raise FirmwareProfileError("artifact and programmed-readback SHA256 must match")

    artifact_size = _required_int(data, "artifact_size_bytes")
    readback_size = _required_int(data, "programmed_readback_bytes")
    if artifact_size != readback_size:
        raise FirmwareProfileError("artifact and programmed-readback sizes must match")

    profile = FirmwareProfile(
        path=p,
        product=_required_str(data, "product"),
        series=_required_str(data, "series"),
        qualified_core_commit=_required_str(data, "qualified_core_commit"),
        qualified_core_tree=_required_str(data, "qualified_core_tree"),
        target_id=_required_str(data, "target_id"),
        expected_identity=_required_str(data, "expected_identity"),
        vendor_build_script=_required_str(data, "vendor_build_script"),
        artifact_relative_path=_required_str(data, "artifact_relative_path"),
        artifact_size_bytes=artifact_size,
        artifact_sha256=artifact_sha,
        flash_base=_required_str(data, "flash_base"),
        programmed_readback_bytes=readback_size,
        programmed_readback_sha256=readback_sha,
        stock_flash_size_bytes=_required_int(data, "stock_flash_size_bytes"),
        stock_flash_sha256=stock_sha,
        expected_bootloader_version=_required_str(data, "expected_bootloader_version").lower(),
        expected_device_id=_required_str(data, "expected_device_id").lower(),
        flash_authorization_token=_required_str(data, "flash_authorization_token"),
        final_write_confirmation=_required_str(data, "final_write_confirmation"),
        state_record=_required_str(data, "state_record"),
    )

    if profile.qualified_core_commit != QUALIFIED_CORE_COMMIT:
        raise FirmwareProfileError("firmware profile core pin does not match product core pin")
    if profile.qualified_core_tree != QUALIFIED_CORE_TREE:
        raise FirmwareProfileError("firmware profile core tree does not match product core tree")
    if profile.vendor_build_script != "vendor/ywd-1278/firmware/build-packet-rssi-ywd1278.py":
        raise FirmwareProfileError("qualified vendor firmware build path changed")
    if not profile.artifact_relative_path.startswith("vendor/ywd-1278/firmware/out/0c-p2-rssi-ax25r4-"):
        raise FirmwareProfileError("qualified firmware artifact path changed")
    if profile.target_id != PRODUCT_TARGET:
        raise FirmwareProfileError("firmware profile target does not match product target")
    if profile.expected_identity != QUALIFIED_FIRMWARE_IDENTITY:
        raise FirmwareProfileError("firmware profile identity does not match product identity")
    if profile.flash_base.lower() != "0x08000000":
        raise FirmwareProfileError("qualified STM32 flash base changed")
    if profile.expected_bootloader_version != "0x22":
        raise FirmwareProfileError("qualified STM32 bootloader version changed")
    if profile.expected_device_id != "0x0410":
        raise FirmwareProfileError("qualified STM32 device ID changed")
    return profile


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_artifact(profile: FirmwareProfile, firmware: str | Path) -> str:
    path = Path(firmware)
    if not path.is_file():
        raise FirmwareProfileError(f"firmware artifact not found: {path}")
    size = path.stat().st_size
    if size != profile.artifact_size_bytes:
        raise FirmwareProfileError(
            f"firmware size mismatch: expected {profile.artifact_size_bytes}, got {size}"
        )
    digest = sha256_file(path)
    if digest != profile.artifact_sha256:
        raise FirmwareProfileError(
            f"firmware SHA256 mismatch: expected {profile.artifact_sha256}, got {digest}"
        )
    return digest


def verify_stock_backup(profile: FirmwareProfile, backup_dir: str | Path) -> str:
    directory = Path(backup_dir)
    manifest = directory / "manifest.json"
    image = directory / "original-flash.bin"
    if not manifest.is_file() or not image.is_file():
        raise FirmwareProfileError("stock backup requires manifest.json and original-flash.bin")
    meta = _load_json(manifest)
    if meta.get("target_id") != profile.target_id:
        raise FirmwareProfileError("stock backup target mismatch")
    if meta.get("read_passes") != 2 or meta.get("two_pass_byte_identical") is not True:
        raise FirmwareProfileError("stock backup lacks verified two-pass identity")
    if meta.get("option_bytes_read_or_written") not in (False, None):
        raise FirmwareProfileError("stock backup touched option bytes")
    if meta.get("flash_written") not in (False, None):
        raise FirmwareProfileError("stock backup unexpectedly records a flash write")
    if int(meta.get("flash_size_bytes", -1)) != profile.stock_flash_size_bytes:
        raise FirmwareProfileError("stock backup flash geometry mismatch")
    digest = sha256_file(image)
    if image.stat().st_size != profile.stock_flash_size_bytes:
        raise FirmwareProfileError("stock backup image size mismatch")
    if digest != profile.stock_flash_sha256:
        raise FirmwareProfileError("stock backup SHA256 does not match qualified stock image")
    if str(meta.get("sha256", "")).lower() != digest:
        raise FirmwareProfileError("stock backup manifest SHA256 mismatch")
    if "stock_sha256_match" in meta and meta.get("stock_sha256_match") is not True:
        raise FirmwareProfileError("stock backup manifest does not prove stock SHA256 match")
    return digest


def verify_programmed_readback(profile: FirmwareProfile, path: str | Path) -> str:
    readback = Path(path)
    if not readback.is_file():
        raise FirmwareProfileError(f"programmed readback not found: {readback}")
    if readback.stat().st_size != profile.programmed_readback_bytes:
        raise FirmwareProfileError("programmed readback size mismatch")
    digest = sha256_file(readback)
    if digest != profile.programmed_readback_sha256:
        raise FirmwareProfileError(
            f"programmed readback SHA256 mismatch: expected "
            f"{profile.programmed_readback_sha256}, got {digest}"
        )
    return digest


def _default_profile() -> Path:
    installed = Path("/opt/ywd-mmdvm-tnc/source/firmware/product-ax25r4.json")
    if installed.is_file():
        return installed
    return Path("firmware/product-ax25r4.json")


def main() -> int:
    parser = argparse.ArgumentParser(prog="ywd-tnc-fw")
    parser.add_argument("--profile", default=str(_default_profile()))
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("show")

    art = sub.add_parser("verify-artifact")
    art.add_argument("--firmware", required=True)

    backup = sub.add_parser("verify-stock-backup")
    backup.add_argument("--backup-dir", required=True)

    rb = sub.add_parser("verify-readback")
    rb.add_argument("--readback", required=True)

    args = parser.parse_args()
    try:
        profile = load_profile(args.profile)
        if args.command == "show":
            print("YWD_TNC_FIRMWARE_PROFILE=PASS")
            print(f"TARGET_ID={profile.target_id}")
            print(f"EXPECTED_IDENTITY={profile.expected_identity}")
            print(f"ARTIFACT_RELATIVE_PATH={profile.artifact_relative_path}")
            print(f"ARTIFACT_SIZE_BYTES={profile.artifact_size_bytes}")
            print(f"ARTIFACT_SHA256={profile.artifact_sha256}")
            print(f"QUALIFIED_CORE_COMMIT={profile.qualified_core_commit}")
        elif args.command == "verify-artifact":
            digest = verify_artifact(profile, args.firmware)
            print("YWD_TNC_FIRMWARE_ARTIFACT=PASS")
            print(f"ARTIFACT_SHA256={digest}")
            print(f"ARTIFACT_SIZE_BYTES={profile.artifact_size_bytes}")
        elif args.command == "verify-stock-backup":
            digest = verify_stock_backup(profile, args.backup_dir)
            print("YWD_TNC_STOCK_BACKUP=PASS")
            print(f"STOCK_BACKUP_SHA256={digest}")
            print("BACKUP_READ_PASSES=2")
            print("OPTION_BYTES_READ_OR_WRITTEN=NO")
        elif args.command == "verify-readback":
            digest = verify_programmed_readback(profile, args.readback)
            print("YWD_TNC_PROGRAMMED_READBACK=PASS")
            print(f"PROGRAMMED_READBACK_SHA256={digest}")
        else:  # pragma: no cover
            raise FirmwareProfileError("unsupported firmware command")
    except FirmwareProfileError as exc:
        print(f"YWD_TNC_FIRMWARE=FAIL:{exc}", file=sys.stderr)
        return 20

    print("MODEM_UART_OPENED=NO")
    print("GPIO_ACCESSED=NO")
    print("FLASH_WRITTEN=NO")
    print("RF_TRANSMITTED=NO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
