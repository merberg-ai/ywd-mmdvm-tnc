"""FM1 hardware-inert firmware inventory and verified backup catalog.

No UART, GPIO, service control, bootloader or subprocess access.
All inventory entries reflect stored manifests; none claim a live HAT probe.
"""
from __future__ import annotations

from pathlib import Path
import json
import os
import re

from ywdtnc.firmware import FirmwareProfileError, load_profile, sha256_file, verify_stock_backup


ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / "firmware/product-ax25r4.json"
TARGETS = ROOT / "firmware/targets.json"
BACKUP_ROOTS = (Path("/var/lib/ywd-mmdvm-tnc/firmware-backups"),
                Path("/var/lib/ywd-1278/firmware-backups"))
SAFE_BACKUP_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
MAX_ENTRIES = 200


def inventory(*, profile_path: Path = PROFILE, targets_path: Path = TARGETS,
              backup_roots: tuple[Path, ...] = BACKUP_ROOTS) -> dict:
    """Describe qualified target and backup files WITHOUT probing a device."""
    profile = load_profile(profile_path)
    target_data = json.loads(targets_path.read_text(encoding="utf-8"))
    matches = [x for x in target_data.get("targets", [])
               if x.get("id") == profile.target_id]
    if target_data.get("schema") != 1 or len(matches) != 1:
        raise ValueError("firmware target manifest is invalid or ambiguous")
    target = matches[0]
    if target.get("expected_device_id") != profile.expected_device_id or target.get("flash_size_bytes") != profile.stock_flash_size_bytes:
        raise ValueError("target geometry/device ID does not match qualified profile")

    entries: list[dict] = []
    inaccessible = False
    truncated = False
    for root in backup_roots:
        base = root / profile.target_id
        if not base.exists():
            # Missing base may also be caused by inaccessible parent paths.
            if root.exists() and not os.access(root, os.R_OK | os.X_OK):
                inaccessible = True
            continue
        try:
            if base.is_symlink() or not base.is_dir():
                raise ValueError("backup target directory must be a real directory")
            dirs = sorted(base.iterdir(), key=lambda p: p.name, reverse=True)
        except PermissionError:
            inaccessible = True
            continue
        for path in dirs:
            if len(entries) >= MAX_ENTRIES:
                truncated = True
                break
            if not SAFE_BACKUP_NAME.fullmatch(path.name) or path.is_symlink() or not path.is_dir():
                continue
            entry = _inspect_backup(path, profile)
            entries.append(entry)
        if truncated:
            break

    return {
        "schema": 1,
        "phase": "FM1",
        "source": "pinned-profile-and-filesystem",
        "live_probe_performed": False,
        "running_firmware": {"state": "not_probed", "identity": None,
                             "reason": "Modem UART is owned by the running TNC"},
        "target": {
            "id": profile.target_id,
            "description": target.get("description", "Qualified MMDVM_HS simplex HAT"),
            "status": "reference_profile_not_live_detected",
            "mcu": target.get("mcu_family"),
            "rf_chip": target.get("rf_chip"),
            "tcxo_mhz": target.get("tcxo_mhz"),
            "stm32_hse_hz": target.get("stm32_hse_hz"),
            "uart": target.get("uart_default"),
            "flash_size_bytes": profile.stock_flash_size_bytes,
            "device_id": profile.expected_device_id,
            "bootloader_version": profile.expected_bootloader_version,
        },
        "qualified_firmware": {
            "series": profile.series,
            "expected_identity": profile.expected_identity,
            "expected_artifact_sha256": profile.artifact_sha256,
            "artifact_size_bytes": profile.artifact_size_bytes,
        },
        "backups": entries,
        "backup_access": "restricted" if inaccessible else "available",
        "backup_results_truncated": truncated,
        "backup_count": len(entries),
        "backup_warning": "Backups under root-only folders may not be visible to this process" if inaccessible else None,
        "capabilities": {"inventory": True, "backup_browser": False, "flash": False,
                         "restore": False, "build_stock": False},
    }


def _inspect_backup(directory: Path, profile) -> dict:
    entry = {"id": directory.name, "kind": "unknown", "integrity": "invalid",
             "restore_qualified": False, "flash_size_bytes": None, "sha256": None,
             "captured_identity": None, "captured_unix": None}
    try:
        manifest_path = directory / "manifest.json"
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise ValueError("missing regular manifest")
        if manifest_path.stat().st_size > 32768:
            raise ValueError("oversized manifest")
        meta = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(meta, dict):
            raise ValueError("invalid manifest")
        entry["captured_identity"] = _bounded_text(meta.get("captured_identity"), 200)
        entry["captured_unix"] = meta.get("captured_unix") if type(meta.get("captured_unix")) is int else None
        if meta.get("schema") == 2 and (directory / "original-flash.bin").is_file():
            if (directory / "original-flash.bin").is_symlink():
                raise ValueError("linked backup image is forbidden")
            digest = verify_stock_backup(profile, directory)
            entry.update(kind="golden_stock", integrity="verified", restore_qualified=True,
                         sha256=digest, flash_size_bytes=profile.stock_flash_size_bytes)
        elif meta.get("schema") == "fws-backup-1":
            image = directory / "captured-flash.bin"
            if image.is_symlink() or not image.is_file():
                raise ValueError("missing regular captured image")
            if meta.get("target_id") != profile.target_id or meta.get("flash_base") != profile.flash_base:
                raise ValueError("incompatible target")
            if meta.get("read_passes") != 2 or meta.get("two_pass_byte_identical") is not True:
                raise ValueError("dual-read evidence missing")
            if meta.get("flash_written") is not False or meta.get("option_bytes_read_or_written") is not False:
                raise ValueError("unexpected hardware mutation")
            if meta.get("flash_size_bytes") != profile.stock_flash_size_bytes or image.stat().st_size != profile.stock_flash_size_bytes:
                raise ValueError("flash geometry mismatch")
            digest = sha256_file(image)
            if digest != meta.get("sha256"):
                raise ValueError("captured firmware checksum mismatch")
            entry.update(kind="captured_firmware", integrity="verified",
                         restore_qualified=False, sha256=digest,
                         flash_size_bytes=profile.stock_flash_size_bytes)
        else:
            raise ValueError("unsupported backup manifest")
    except (OSError, ValueError, TypeError, KeyError, FirmwareProfileError, json.JSONDecodeError):
        pass
    return entry


def _bounded_text(value: object, limit: int) -> str | None:
    return value[:limit] if isinstance(value, str) else None
