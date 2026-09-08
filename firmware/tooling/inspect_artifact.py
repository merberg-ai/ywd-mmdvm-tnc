#!/usr/bin/env python3
"""Inspect a built YWD-1278 STM32F103 firmware binary without hardware access."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import struct


def load_manifest(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != 1:
        raise RuntimeError("unsupported firmware build manifest schema")
    return data


def main() -> int:
    ap = argparse.ArgumentParser(description="Inspect a YWD-1278 firmware build artifact")
    ap.add_argument("binary")
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    binary = Path(args.binary).resolve()
    manifest = load_manifest(Path(args.manifest).resolve())
    data = binary.read_bytes()
    expected_identity = manifest["branding"]["expected_identity"]
    max_size = int(manifest["build"]["max_artifact_bytes"])

    if len(data) < 8:
        raise RuntimeError("firmware binary is too short to contain a vector table")
    if len(data) > max_size:
        raise RuntimeError(f"firmware binary exceeds target maximum: {len(data)} > {max_size}")

    sp, reset = struct.unpack_from("<II", data, 0)
    if not (0x20000000 <= sp <= 0x20010000):
        raise RuntimeError(f"implausible STM32 SRAM initial stack pointer: 0x{sp:08x}")
    if not (reset & 1):
        raise RuntimeError(f"reset vector is not a Thumb address: 0x{reset:08x}")
    reset_addr = reset & ~1
    if not (0x08000000 <= reset_addr < 0x08020000):
        raise RuntimeError(f"reset vector is outside STM32F103 128 KiB application range: 0x{reset:08x}")

    identity_bytes = expected_identity.encode("ascii")
    identity_count = data.count(identity_bytes)
    if identity_count != 1:
        raise RuntimeError(
            f"expected firmware identity occurs {identity_count} times in binary; expected exactly once"
        )

    sha256 = hashlib.sha256(data).hexdigest()
    result = {
        "artifact": str(binary),
        "size_bytes": len(data),
        "sha256": sha256,
        "initial_sp": f"0x{sp:08x}",
        "reset_vector": f"0x{reset:08x}",
        "identity": expected_identity,
        "identity_occurrences": identity_count,
        "rf_configured": False,
        "flash_written": False,
        "option_bytes_written": False,
    }

    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print("YWD1278_ARTIFACT_INSPECTION=PASS")
        print(f"ARTIFACT_SIZE_BYTES={len(data)}")
        print(f"ARTIFACT_SHA256={sha256}")
        print(f"VECTOR_INITIAL_SP=0x{sp:08x}")
        print(f"VECTOR_RESET=0x{reset:08x}")
        print(f"ARTIFACT_IDENTITY={expected_identity}")
        print("ARTIFACT_IDENTITY_COUNT=1")
        print("RF_CONFIGURED=NO")
        print("FLASH_WRITTEN=NO")
        print("OPTION_BYTES_WRITTEN=NO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
