#!/usr/bin/env python3
"""Safe MMDVM_HS application identity probe for YWD-1278.

The probe always tries MMDVM GET_VERSION (0x00) first. By default, if the UART
is genuinely silent and the installed configuration explicitly names an
allowlisted hardware target, it may invoke that target's qualified application-
release operation (BOOT0 normal, RESET released, no reset pulse) and retry.
Callers that manage GPIO recovery themselves can use --no-application-release.

A valid but unknown GET_VERSION identity is still a successful *read*. It is
reported as unsupported/UNKNOWN and is never treated as silence.

The probe never configures RF, changes frequency, enters the STM32 bootloader,
writes flash, or writes option bytes.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import termios
import time

START = 0xE0
GET_VERSION = 0x00
REQUEST = bytes([START, 3, GET_VERSION])

FIRMWARE_DESCRIPTIONS = {
    "STOCK": "Recognized stock MMDVM_HS firmware",
    "YWD1278": "Recognized YWD-1278 firmware",
    "YWD_ENGINEERING": "Recognized pre-product YWD engineering firmware",
    "KNOWN_OTHER": "Recognized allowlisted firmware",
    "UNKNOWN": "Firmware answered GET_VERSION but its identity is not recognized",
    "AMBIGUOUS": "Firmware identity matches more than one hardware target",
}


def configure(fd: int) -> None:
    attrs = termios.tcgetattr(fd)
    attrs[0] = 0
    attrs[1] = 0
    attrs[2] = termios.CS8 | termios.CLOCAL | termios.CREAD
    attrs[3] = 0
    attrs[4] = termios.B115200
    attrs[5] = termios.B115200
    attrs[6][termios.VMIN] = 0
    attrs[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    termios.tcflush(fd, termios.TCIOFLUSH)


def read_frame(fd: int, timeout: float) -> bytes:
    """Read one MMDVM frame, tolerating junk/partial data before START."""
    data = bytearray()
    target: int | None = None
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        remaining = max(0.0, deadline - time.monotonic())
        ready, _, _ = select.select([fd], [], [], min(0.1, remaining))
        if fd not in ready:
            continue

        chunk = os.read(fd, 512)
        for byte in chunk:
            if not data:
                if byte == START:
                    data.append(byte)
                continue

            if len(data) == 1:
                if byte < 3:
                    data.clear()
                    target = None
                    if byte == START:
                        data.append(byte)
                    continue
                data.append(byte)
                target = byte
                continue

            data.append(byte)
            if target is not None and len(data) >= target:
                return bytes(data[:target])

    return bytes(data)


def parse_identity(reply: bytes) -> str | None:
    if len(reply) < 5 or reply[0] != START or reply[2] != GET_VERSION:
        return None
    return reply[4:].split(b"\0", 1)[0].decode("ascii", "replace").strip()


def get_identity(
    device: str,
    timeout: float,
    attempts: int = 3,
    settle_seconds: float = 0.10,
) -> str:
    if attempts < 1:
        raise ValueError("attempts must be >= 1")

    fd = os.open(device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    observed: list[bytes] = []
    try:
        configure(fd)
        time.sleep(max(0.0, settle_seconds))

        for attempt in range(1, attempts + 1):
            termios.tcflush(fd, termios.TCIOFLUSH)
            written = os.write(fd, REQUEST)
            if written != len(REQUEST):
                raise RuntimeError(
                    f"short GET_VERSION write on attempt {attempt}: {written}/{len(REQUEST)}"
                )
            termios.tcdrain(fd)

            reply = read_frame(fd, timeout)
            observed.append(reply)
            identity = parse_identity(reply)
            if identity:
                return identity

            if attempt < attempts:
                time.sleep(0.10)
    finally:
        os.close(fd)

    details = ", ".join(
        f"attempt{i + 1}={frame.hex(' ') if frame else '<none>'}"
        for i, frame in enumerate(observed)
    )
    raise RuntimeError(f"invalid/no GET_VERSION response after {attempts} attempts: {details}")


def load_targets(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != 1 or not isinstance(payload.get("targets"), list):
        raise RuntimeError("unsupported targets manifest schema")
    return payload["targets"]


def identity_class_for_target(identity: str, target: dict) -> str | None:
    if identity in (target.get("stock_identities") or []):
        return "STOCK"
    if identity in (target.get("engineering_identities") or []):
        return "YWD_ENGINEERING"
    prefix = target.get("ywd1278_identity_prefix")
    if isinstance(prefix, str) and prefix and identity.startswith(prefix):
        return "YWD1278"
    if identity in (target.get("accepted_running_identities") or []):
        return "KNOWN_OTHER"
    return None


def classify_identity(identity: str, targets: list[dict]) -> tuple[list[dict], str]:
    matches: list[dict] = []
    classes: list[str] = []
    for target in targets:
        firmware_class = identity_class_for_target(identity, target)
        if firmware_class is not None:
            matches.append(target)
            classes.append(firmware_class)

    if not matches:
        return [], "UNKNOWN"
    if len(matches) != 1:
        return matches, "AMBIGUOUS"
    return matches, classes[0]


def release_configured_application(config: Path, targets: Path) -> str:
    helper = Path(__file__).with_name("hat_control.py")
    proc = subprocess.run(
        [
            sys.executable,
            str(helper),
            "application-release",
            "--targets",
            str(targets),
            "--config",
            str(config),
        ],
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "target-aware application release failed").strip()
        raise RuntimeError(detail)
    return proc.stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser(description="Safe YWD-1278 MMDVM HAT identity probe")
    ap.add_argument("--device", default="/dev/ttyAMA0")
    ap.add_argument("--targets", default=str(Path(__file__).with_name("targets.json")))
    ap.add_argument("--config", default="/etc/ywd-1278/config.toml")
    ap.add_argument("--timeout", type=float, default=1.25, help="per-attempt response timeout")
    ap.add_argument("--attempts", type=int, default=3)
    ap.add_argument("--no-application-release", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    target_path = Path(args.targets)
    control_output = ""
    application_release_used = False

    try:
        identity = get_identity(args.device, args.timeout, args.attempts)
    except RuntimeError as first_error:
        if args.no_application_release:
            raise
        config_path = Path(args.config)
        if not config_path.is_file():
            raise RuntimeError(
                f"{first_error}; no configured hardware target is available for safe application release"
            ) from first_error
        try:
            control_output = release_configured_application(config_path, target_path)
        except RuntimeError as release_error:
            raise RuntimeError(f"{first_error}; {release_error}") from first_error
        application_release_used = True
        time.sleep(0.25)
        identity = get_identity(args.device, args.timeout, args.attempts)

    targets = load_targets(target_path)
    matches, firmware_class = classify_identity(identity, targets)
    firmware_description = FIRMWARE_DESCRIPTIONS[firmware_class]

    result = {
        "device": args.device,
        "identity": identity,
        "matched_target_ids": [item["id"] for item in matches],
        "unique_supported_identity": len(matches) == 1,
        "firmware_class": firmware_class,
        "firmware_description": firmware_description,
        "firmware_known": firmware_class not in {"UNKNOWN", "AMBIGUOUS"},
        "application_release_used": application_release_used,
        "rf_configured": False,
        "flash_written": False,
        "option_bytes_written": False,
    }

    if args.json:
        # JSON mode means "did GET_VERSION return a parseable identity?". Support
        # status is carried explicitly in the result instead of being confused
        # with UART silence. This lets installers report unknown firmware without
        # manipulating GPIO merely because the identity is unfamiliar.
        print(json.dumps(result, sort_keys=True))
        return 0

    if control_output:
        print(control_output)
    print(f"HAT_APPLICATION_RELEASE_USED={'YES' if application_release_used else 'NO'}")
    print(f"HAT_DEVICE={args.device}")
    print(f"HAT_IDENTITY={identity}")
    print(f"HAT_FIRMWARE_CLASS={firmware_class}")
    print(f"HAT_FIRMWARE_DESCRIPTION={firmware_description}")
    if len(matches) == 1:
        print(f"HAT_TARGET_MATCH={matches[0]['id']}")
        print("HAT_TARGET_IDENTITY=PASS")
    elif not matches:
        print("HAT_TARGET_MATCH=NONE")
        print("HAT_TARGET_IDENTITY=UNSUPPORTED")
    else:
        print("HAT_TARGET_MATCH=AMBIGUOUS")
        print("HAT_TARGET_IDENTITY=FAIL")
    print("RF_CONFIGURED=NO")
    print("FLASH_WRITTEN=NO")
    print("OPTION_BYTES_WRITTEN=NO")

    return 0 if len(matches) == 1 else 3


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"HAT_PROBE_ERROR={exc}", file=sys.stderr)
        raise SystemExit(2)
