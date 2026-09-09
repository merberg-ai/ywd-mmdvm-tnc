#!/usr/bin/env python3
"""Target-aware Raspberry Pi control-line helper for supported YWD-1278 HATs.

This helper controls only manifest-qualified Raspberry Pi GPIO lines. It never
opens the modem UART, configures RF, reads or writes STM32 flash, or touches
option bytes.

Supported operations intentionally distinguish between:

* application-release: BOOT0 to the application level and RESET released,
  without pulsing RESET. This is used to recover a HAT that booted with RESET
  held low by the host pinmux.
* bootloader-entry: BOOT0 to the qualified system-bootloader level followed by
  one explicit RESET low/high pulse.
* application-restart: BOOT0 to the application level followed by one explicit
  RESET low/high pulse so a running system bootloader exits into the app.

Reset-pulsing operations are available only for an explicit allowlisted target
whose manifest opts into the exact GPIO behavior.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import tomllib


def load_targets(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != 1 or not isinstance(payload.get("targets"), list):
        raise RuntimeError("unsupported targets manifest schema")
    return payload["targets"]


def config_target(path: Path) -> str:
    if not path.exists():
        return ""
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    target = data.get("hardware", {}).get("target", "")
    return target if isinstance(target, str) else ""


def read_model() -> str:
    path = Path("/proc/device-tree/model")
    if not path.exists():
        raise RuntimeError("Raspberry Pi model is unavailable")
    return path.read_bytes().replace(b"\0", b"").decode("ascii", "replace").strip()


def find_target(target_id: str, targets: list[dict]) -> dict:
    matches = [item for item in targets if item.get("id") == target_id]
    if len(matches) != 1:
        raise RuntimeError(f"unknown or ambiguous hardware target: {target_id or '<empty>'}")
    return matches[0]


def compatible_auto_release_targets(targets: list[dict]) -> list[dict]:
    model = read_model()
    matches: list[dict] = []
    for item in targets:
        control = item.get("host_control")
        if not isinstance(control, dict):
            continue
        expected = control.get("platform_model_contains")
        if (
            control.get("installer_auto_release_candidate") is True
            and isinstance(expected, str)
            and expected
            and expected in model
        ):
            matches.append(item)
    if not matches:
        raise RuntimeError(f"no installer auto-release profile is qualified for host '{model}'")

    # Auto-release remains the no-pulse recovery path. Adding a second
    # incompatible candidate makes this path fail closed.
    keys = (
        "tool",
        "boot0_gpio",
        "reset_gpio",
        "application_boot0_level",
        "application_reset_level",
        "application_release_pulses_reset",
    )
    signatures = {tuple(item["host_control"].get(k) for k in keys) for item in matches}
    if len(signatures) != 1:
        raise RuntimeError("compatible targets disagree on automatic application-release GPIO behavior")
    return matches


def pinctrl_get(tool: str, gpio: int) -> str:
    proc = subprocess.run([tool, "get", str(gpio)], check=True, text=True, capture_output=True)
    return proc.stdout.strip()


def pinctrl_set(tool: str, gpio: int, level: str) -> None:
    if level not in {"low", "high"}:
        raise RuntimeError(f"unsupported GPIO level in target manifest: {level}")
    subprocess.run([tool, "set", str(gpio), "op", "dl" if level == "low" else "dh"], check=True)


def control_context(control: dict) -> tuple[str, str, int, int]:
    expected_model = control.get("platform_model_contains")
    if not isinstance(expected_model, str) or not expected_model:
        raise RuntimeError("target host_control lacks platform_model_contains")
    model = read_model()
    if expected_model not in model:
        raise RuntimeError(f"host model mismatch: expected '{expected_model}' in '{model}'")

    if control.get("tool") != "pinctrl":
        raise RuntimeError(f"unsupported host-control tool: {control.get('tool')!r}")
    tool = shutil.which("pinctrl")
    if not tool:
        raise RuntimeError("pinctrl is required for this hardware target")

    boot0 = control.get("boot0_gpio")
    reset = control.get("reset_gpio")
    if not isinstance(boot0, int) or not isinstance(reset, int):
        raise RuntimeError("target host_control GPIO numbers are invalid")
    return model, tool, boot0, reset


def common_before(label: str, control: dict) -> tuple[str, str, int, int]:
    model, tool, boot0, reset = control_context(control)
    print(f"HAT_CONTROL_TARGET={label}")
    print(f"HAT_CONTROL_HOST={model}")
    print(f"HAT_CONTROL_BOOT0_BEFORE={pinctrl_get(tool, boot0)}")
    print(f"HAT_CONTROL_RESET_BEFORE={pinctrl_get(tool, reset)}")
    return model, tool, boot0, reset


def common_after(tool: str, boot0: int, reset: int) -> None:
    print(f"HAT_CONTROL_BOOT0_AFTER={pinctrl_get(tool, boot0)}")
    print(f"HAT_CONTROL_RESET_AFTER={pinctrl_get(tool, reset)}")
    print("MODEM_UART_OPENED=NO")
    print("RF_CONFIGURED=NO")
    print("FLASH_WRITTEN=NO")
    print("OPTION_BYTES_WRITTEN=NO")


def pulse_reset(tool: str, reset: int, control: dict) -> None:
    assert_level = control.get("reset_assert_level")
    release_level = control.get("reset_release_level")
    pulse_seconds = control.get("reset_pulse_seconds")
    if assert_level != "low" or release_level != "high":
        raise RuntimeError("qualified RESET pulse must assert low and release high")
    if not isinstance(pulse_seconds, (int, float)) or not (0.05 <= float(pulse_seconds) <= 1.0):
        raise RuntimeError("target reset_pulse_seconds is outside the qualified safety range")
    pinctrl_set(tool, reset, "low")
    time.sleep(float(pulse_seconds))
    pinctrl_set(tool, reset, "high")


def release_with_profile(label: str, control: dict) -> None:
    _, tool, boot0, reset = common_before(label, control)
    boot0_level = control.get("application_boot0_level")
    reset_level = control.get("application_reset_level")
    if control.get("application_release_pulses_reset") is not False:
        raise RuntimeError("application release must be explicitly qualified as no-reset-pulse")
    pinctrl_set(tool, boot0, str(boot0_level))
    pinctrl_set(tool, reset, str(reset_level))
    common_after(tool, boot0, reset)
    print("HAT_APPLICATION_STATE_RELEASED=YES")
    print("STM32_RESET_PULSED=NO")


def application_release(target: dict) -> None:
    control = target.get("host_control")
    if not isinstance(control, dict):
        raise RuntimeError("target has no qualified host_control definition")
    release_with_profile(str(target["id"]), control)


def bootloader_entry(target: dict) -> None:
    if target.get("bootloader_entry") != "pi-gpio20-21":
        raise RuntimeError("target does not opt into qualified Pi GPIO bootloader entry")
    control = target.get("host_control")
    if not isinstance(control, dict):
        raise RuntimeError("target has no qualified host_control definition")
    if control.get("bootloader_entry_pulses_reset") is not True:
        raise RuntimeError("target bootloader entry is not qualified for an explicit RESET pulse")

    _, tool, boot0, reset = common_before(str(target["id"]), control)
    if control.get("bootloader_boot0_level") != "high":
        raise RuntimeError("qualified STM32 system bootloader entry requires BOOT0 high")
    pinctrl_set(tool, boot0, "high")
    pulse_reset(tool, reset, control)
    common_after(tool, boot0, reset)
    print("HAT_BOOTLOADER_STATE_REQUESTED=YES")
    print("STM32_RESET_PULSED=YES")


def application_restart(target: dict) -> None:
    control = target.get("host_control")
    if not isinstance(control, dict):
        raise RuntimeError("target has no qualified host_control definition")
    if control.get("application_restart_pulses_reset") is not True:
        raise RuntimeError("target application restart is not qualified for an explicit RESET pulse")

    _, tool, boot0, reset = common_before(str(target["id"]), control)
    if control.get("application_boot0_level") != "low":
        raise RuntimeError("qualified STM32 application restart requires BOOT0 low")
    pinctrl_set(tool, boot0, "low")
    pulse_reset(tool, reset, control)
    common_after(tool, boot0, reset)
    print("HAT_APPLICATION_RESTARTED=YES")
    print("STM32_RESET_PULSED=YES")


def auto_detect_release(targets: list[dict]) -> None:
    candidates = compatible_auto_release_targets(targets)
    ids = ",".join(str(item["id"]) for item in candidates)
    print(f"HAT_CONTROL_CANDIDATES={ids}")
    release_with_profile("AUTO-CANDIDATE", candidates[0]["host_control"])


def main() -> int:
    ap = argparse.ArgumentParser(description="YWD-1278 target-aware HAT control")
    ap.add_argument(
        "operation",
        choices=["application-release", "auto-detect-release", "bootloader-entry", "application-restart"],
    )
    ap.add_argument("--targets", default=str(Path(__file__).with_name("targets.json")))
    ap.add_argument("--target", default="")
    ap.add_argument("--config", default="")
    args = ap.parse_args()

    targets = load_targets(Path(args.targets))
    if args.operation == "auto-detect-release":
        auto_detect_release(targets)
        return 0

    target_id = args.target
    if not target_id and args.config:
        target_id = config_target(Path(args.config))
    if not target_id:
        raise RuntimeError("hardware target is required; use --target or configure [hardware].target")
    target = find_target(target_id, targets)

    if args.operation == "application-release":
        application_release(target)
    elif args.operation == "bootloader-entry":
        bootloader_entry(target)
    elif args.operation == "application-restart":
        application_restart(target)
    else:  # pragma: no cover - argparse constrains this
        raise RuntimeError("unsupported HAT control operation")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"HAT_CONTROL_ERROR={exc}", file=sys.stderr)
        raise SystemExit(2)
