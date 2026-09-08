#!/usr/bin/env python3
"""Apply YWD-1278 branding to the deterministic AX25R4 RSSI candidate.

The complete frozen AX25R3 engineering chain plus the pinned AX25R4 RSSI
telemetry transform must already have succeeded. This step changes only the
user-visible engineering identity/info strings to the YWD-1278 product strings.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

EXPECTED_TRACKED = [
    "ADF7021.cpp",
    "Config.h",
    "Globals.h",
    "IO.cpp",
    "IO.h",
    "IOSTM.cpp",
    "MMDVM_HS.cpp",
    "SerialPort.cpp",
    "version.h",
]
EXPECTED_UNTRACKED = [
    "AX25AFSKRX.cpp",
    "AX25AFSKRX.h",
    "AX25AFSKTX.cpp",
    "AX25AFSKTX.h",
]


def git_lines(src: Path, *args: str) -> list[str]:
    out = subprocess.check_output(["git", "-C", str(src), *args], text=True)
    return sorted(line for line in out.splitlines() if line)


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("source", type=Path)
    ap.add_argument("--manifest", type=Path, required=True)
    args = ap.parse_args()

    src = args.source.resolve()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    branding = manifest["branding"]
    telemetry = manifest["telemetry"]
    legacy_version = branding["legacy_version_token"]
    legacy_info = branding["legacy_info"]
    expected_info = branding["expected_info"]
    series = branding["product_series"]
    firmware_version = branding["firmware_version"]
    new_version = f"YWD-1278-{series}-v{firmware_version}"

    if git_lines(src, "diff", "--name-only") != sorted(EXPECTED_TRACKED):
        raise RuntimeError("RSSI branding requires the exact AX25R4 transformed tracked-file set")
    if git_lines(src, "ls-files", "--others", "--exclude-standard") != sorted(EXPECTED_UNTRACKED):
        raise RuntimeError("RSSI branding requires the exact AX25R4 generated-file set")
    if (src / "Config.h").read_bytes() != (src / "configs/MMDVM_HS_Hat.h").read_bytes():
        raise RuntimeError("Config.h differs from the pinned simplex-HAT template")

    adf = (src / "ADF7021.cpp").read_text(encoding="utf-8")
    iostm = (src / "IOSTM.cpp").read_text(encoding="utf-8")
    tx = (src / "AX25AFSKTX.cpp").read_text(encoding="utf-8")
    serial_path = src / "SerialPort.cpp"
    version_path = src / "version.h"
    serial = serial_path.read_text(encoding="utf-8")
    version = version_path.read_text(encoding="utf-8")

    required = [
        (legacy_version in version, "AX25R4 engineering identity"),
        (legacy_info in serial, "AX25R4 engineering info string"),
        ('#define SEND_RSSI_DATA' in (src / "Config.h").read_text(), "RSSI-enabled pinned HAT config"),
        ("uint16_t CIO::readRSSI()" in adf and "AD7021_RB = 0x0147" in adf, "ADF7021 RSSI ADC readback"),
        ("YWD_RX_RSSI        = 0x05U" in serial, "YWD_RX RSSI opcode"),
        ("const uint16_t rssi = io.readRSSI();" in serial, "read-only RSSI source"),
        ("!ax25AFSKRX.active() || m_tx || ax25AFSKTX.busy()" in serial, "RSSI RX-only/TX-idle gate"),
        ("reply[4U] = 3U" in serial, "RX status revision 3 unchanged"),
        ("0x000E006FU" in adf, "Register-15 CDR bypass"),
        ("5U                         << 20" in adf, "RX3 post-demod filter"),
        ("TIM2" in iostm and "19200U" in iostm, "TIM2 19.2 ksps sampler"),
        ("CIO_FIFO_RESERVE = 256U" in tx, "qualified TX FIFO reserve"),
    ]
    missing = [label for ok, label in required if not ok]
    if missing:
        raise RuntimeError("AX25R4 behavior anchor missing: " + ", ".join(missing))

    if telemetry["subcommand"] != 5 or telemetry["carrier_threshold_selected"] is not False:
        raise RuntimeError("RSSI telemetry manifest does not preserve raw/no-threshold staging")

    version = replace_once(version, legacy_version, new_version, "product firmware identity")
    serial = replace_once(serial, legacy_info, expected_info, "product info string")
    version_path.write_text(version, encoding="utf-8")
    serial_path.write_text(serial, encoding="utf-8")

    if git_lines(src, "diff", "--name-only") != sorted(EXPECTED_TRACKED):
        raise RuntimeError("product branding unexpectedly changed the transformed tracked-file set")
    if git_lines(src, "ls-files", "--others", "--exclude-standard") != sorted(EXPECTED_UNTRACKED):
        raise RuntimeError("product branding unexpectedly changed generated files")
    if (src / "Config.h").read_bytes() != (src / "configs/MMDVM_HS_Hat.h").read_bytes():
        raise RuntimeError("Config.h changed during product branding")

    subprocess.check_call(["git", "-C", str(src), "diff", "--check"])
    print("YWD1278_PACKET_RSSI_BRANDING_TRANSFORM=PASS")
    print(f"LEGACY_VERSION_TOKEN={legacy_version}")
    print(f"PRODUCT_VERSION_TOKEN={new_version}")
    print(f"PRODUCT_INFO={expected_info}")
    print("FROZEN_AX25R3_BEHAVIOR_ANCHORS=PASS")
    print("AX25R4_RSSI_TELEMETRY_ANCHORS=PASS")
    print("RSSI_THRESHOLD_SELECTED=NO")
    print("RF_CONFIGURED=NO")
    print("FLASH_WRITTEN=NO")
    print("OPTION_BYTES_WRITTEN=NO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
