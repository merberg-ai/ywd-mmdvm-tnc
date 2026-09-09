"""Operator CLI for safe YWD-MMDVM-TNC RF profile switching."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile

from . import (
    APRS_TX_FREQUENCY_HZ,
    APRS_TX_POWER,
    QUALIFIED_TX_FREQUENCY_HZ,
    QUALIFIED_TX_POWER,
)
from .config import TNCConfig, load_config


DEFAULT_CONFIG = Path("/etc/ywd-mmdvm-tnc/config.toml")
DEFAULT_SERVICE = "ywd-mmdvm-tnc.service"

PROFILE_SETTINGS = {
    "packet": (QUALIFIED_TX_FREQUENCY_HZ, QUALIFIED_TX_POWER, True),
    "aprs": (APRS_TX_FREQUENCY_HZ, APRS_TX_POWER, True),
}

_RADIO_KEYS = ("frequency_mhz", "tx_power", "tx_enabled")


def _format_frequency_mhz(frequency_hz: int) -> str:
    return f"{frequency_hz / 1_000_000:.3f}"


def rewrite_radio_settings(
    text: str,
    *,
    frequency_hz: int,
    tx_power: int,
    tx_enabled: bool,
) -> str:
    """Replace only the three [radio] profile values and preserve the rest."""

    replacements = {
        "frequency_mhz": _format_frequency_mhz(frequency_hz),
        "tx_power": str(int(tx_power)),
        "tx_enabled": "true" if tx_enabled else "false",
    }
    seen: set[str] = set()
    in_radio = False
    output: list[str] = []

    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_radio = stripped == "[radio]"
            output.append(line)
            continue

        replaced = False
        if in_radio:
            for key in _RADIO_KEYS:
                match = re.match(rf"^(\s*){re.escape(key)}\s*=.*?(\r?\n)?$", line)
                if match:
                    if key in seen:
                        raise ValueError(f"duplicate radio.{key} setting")
                    newline = match.group(2) or ""
                    output.append(f"{match.group(1)}{key} = {replacements[key]}{newline}")
                    seen.add(key)
                    replaced = True
                    break
        if not replaced:
            output.append(line)

    missing = set(_RADIO_KEYS) - seen
    if missing:
        names = ", ".join(sorted(f"radio.{key}" for key in missing))
        raise ValueError(f"configuration is missing required setting(s): {names}")
    return "".join(output)


def _validate_candidate(text: str) -> TNCConfig:
    with tempfile.NamedTemporaryFile("w", suffix=".toml", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        return load_config(handle.name)


def _atomic_write(path: Path, text: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as handle:
            temp_name = handle.name
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), stat.S_IMODE(mode))
        os.replace(temp_name, path)
        temp_name = ""
    finally:
        if temp_name:
            Path(temp_name).unlink(missing_ok=True)


def identify_profile(config: TNCConfig) -> str:
    if not config.tx_enabled:
        return "rx-only"
    if (config.frequency_hz, config.tx_power) == (
        QUALIFIED_TX_FREQUENCY_HZ,
        QUALIFIED_TX_POWER,
    ):
        return "packet"
    if (config.frequency_hz, config.tx_power) == (APRS_TX_FREQUENCY_HZ, APRS_TX_POWER):
        return "aprs"
    return "unknown"


def _service_state(service: str) -> str:
    result = subprocess.run(
        ["systemctl", "is-active", service],
        check=False,
        capture_output=True,
        text=True,
    )
    value = result.stdout.strip() or result.stderr.strip()
    return value or f"exit-{result.returncode}"


def _print_status(config: TNCConfig, *, service_state: str) -> None:
    print("YWD-MMDVM-TNC RF profile")
    print(f"Profile:   {identify_profile(config)}")
    print(f"Frequency: {_format_frequency_mhz(config.frequency_hz)} MHz")
    print(f"TX:        {'ENABLED' if config.tx_enabled else 'DISABLED'}")
    print(f"Power:     {config.tx_power}")
    if config.tx_enabled and config.frequency_hz == QUALIFIED_TX_FREQUENCY_HZ:
        print("Status:    physically-qualified packet profile")
    elif config.tx_enabled and config.frequency_hz == APRS_TX_FREQUENCY_HZ:
        print("Status:    permitted APRS profile; not separately physically qualified")
    else:
        print("Status:    receive-only")
    print(f"Service:   {service_state}")


def apply_profile(
    profile: str,
    *,
    config_path: Path = DEFAULT_CONFIG,
    service: str = DEFAULT_SERVICE,
    restart: bool = True,
) -> TNCConfig:
    current = load_config(config_path)
    if profile == "rx-only":
        frequency_hz, tx_power, tx_enabled = (
            current.frequency_hz,
            current.tx_power,
            False,
        )
    else:
        try:
            frequency_hz, tx_power, tx_enabled = PROFILE_SETTINGS[profile]
        except KeyError as exc:
            raise ValueError(f"unknown RF profile: {profile}") from exc

    original = config_path.read_text(encoding="utf-8")
    candidate_text = rewrite_radio_settings(
        original,
        frequency_hz=frequency_hz,
        tx_power=tx_power,
        tx_enabled=tx_enabled,
    )
    candidate = _validate_candidate(candidate_text)

    mode = config_path.stat().st_mode
    backup_path = Path(f"{config_path}.bak")
    shutil.copy2(config_path, backup_path)
    _atomic_write(config_path, candidate_text, mode)

    if restart:
        try:
            subprocess.run(["systemctl", "restart", service], check=True)
        except BaseException:
            _atomic_write(config_path, original, mode)
            subprocess.run(["systemctl", "restart", service], check=False)
            raise

    return candidate


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ywd-tnc-profile",
        description="Switch YWD-MMDVM-TNC between bounded RF operating profiles.",
    )
    parser.add_argument("profile", choices=("packet", "aprs", "rx-only", "status"))
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--service", default=DEFAULT_SERVICE)
    parser.add_argument(
        "--no-restart",
        action="store_true",
        help="write and validate the configuration without restarting systemd",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.profile == "status":
            config = load_config(args.config)
            _print_status(config, service_state=_service_state(args.service))
            return 0

        config = apply_profile(
            args.profile,
            config_path=args.config,
            service=args.service,
            restart=not args.no_restart,
        )
        service_state = "not-restarted" if args.no_restart else _service_state(args.service)
        _print_status(config, service_state=service_state)
        return 0
    except PermissionError as exc:
        _parser().error(f"permission denied ({exc}); run with sudo for the installed configuration")
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        _parser().error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
