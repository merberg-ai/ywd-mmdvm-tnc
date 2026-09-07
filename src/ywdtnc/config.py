"""Configuration for the modem-only YWD-MMDVM-TNC appliance."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import ipaddress
from pathlib import Path
import tomllib

from ywd1278.modem.rx_config import validate_rx_frequency_hz

from . import (
    PRODUCT_TARGET,
    QUALIFIED_FIRMWARE_IDENTITY,
    QUALIFIED_TX_FREQUENCY_HZ,
    QUALIFIED_TX_POWER,
)


class TNCConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class ListenerConfig:
    enabled: bool
    listen: str
    port: int
    allow_wildcard_bind: bool = False


@dataclass(frozen=True)
class TNCConfig:
    target: str
    device: str
    frequency_hz: int
    tx_power: int
    tx_enabled: bool
    txdelay: int
    persist: int
    slottime: int
    kiss: ListenerConfig
    agw: ListenerConfig
    agw_raw_only: bool
    required_identity: str


def _table(root: dict, name: str) -> dict:
    value = root.get(name)
    if not isinstance(value, dict):
        raise TNCConfigurationError(f"missing or invalid [{name}] table")
    return value


def _string(table: dict, key: str) -> str:
    value = table.get(key)
    if not isinstance(value, str):
        raise TNCConfigurationError(f"{key} must be a string")
    return value.strip()


def _bool(table: dict, key: str) -> bool:
    value = table.get(key)
    if not isinstance(value, bool):
        raise TNCConfigurationError(f"{key} must be true or false")
    return value


def _optional_bool(table: dict, key: str, *, default: bool = False) -> bool:
    value = table.get(key, default)
    if not isinstance(value, bool):
        raise TNCConfigurationError(f"{key} must be true or false")
    return value


def _int(table: dict, key: str) -> int:
    value = table.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise TNCConfigurationError(f"{key} must be an integer")
    return int(value)


def _frequency_hz(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TNCConfigurationError("radio.frequency_mhz must be numeric")
    try:
        hz_value = Decimal(str(value)) * Decimal(1_000_000)
    except InvalidOperation as exc:
        raise TNCConfigurationError("radio.frequency_mhz is invalid") from exc
    if hz_value != hz_value.to_integral_value():
        raise TNCConfigurationError("radio.frequency_mhz must resolve to an integer Hz value")
    try:
        return validate_rx_frequency_hz(int(hz_value))
    except ValueError as exc:
        raise TNCConfigurationError(str(exc)) from exc


def _listener(table: dict, label: str) -> ListenerConfig:
    config = ListenerConfig(
        enabled=_bool(table, "enabled"),
        listen=_string(table, "listen"),
        port=_int(table, "port"),
        allow_wildcard_bind=_optional_bool(table, "allow_wildcard_bind"),
    )
    if not 1 <= config.port <= 65535:
        raise TNCConfigurationError(f"{label}.port must be 1..65535")
    try:
        address = ipaddress.ip_address(config.listen)
    except ValueError as exc:
        raise TNCConfigurationError(
            f"{label}.listen must be an IPv4 loopback/private address or explicitly authorized 0.0.0.0"
        ) from exc
    if address.version != 4:
        raise TNCConfigurationError(f"{label}.listen must be IPv4")
    if address.is_multicast:
        raise TNCConfigurationError(f"{label}.listen may not be a multicast address")
    if address.is_unspecified:
        if config.listen != "0.0.0.0" or not config.allow_wildcard_bind:
            raise TNCConfigurationError(
                f"{label}.listen=0.0.0.0 requires {label}.allow_wildcard_bind=true"
            )
        return config
    if not (address.is_loopback or address.is_private):
        raise TNCConfigurationError(
            f"{label}.listen must be an IPv4 loopback/private address; public binds are not permitted"
        )
    return config


def validate_config(config: TNCConfig) -> None:
    if config.target != PRODUCT_TARGET:
        raise TNCConfigurationError(f"hardware.target must be {PRODUCT_TARGET!r}")
    if not config.device.startswith("/dev/"):
        raise TNCConfigurationError("radio.device must be an absolute /dev path")
    try:
        validate_rx_frequency_hz(config.frequency_hz)
    except ValueError as exc:
        raise TNCConfigurationError(str(exc)) from exc
    if not 0 <= config.tx_power <= 255:
        raise TNCConfigurationError("radio.tx_power must be 0..255")
    if not 0 <= config.txdelay <= 255:
        raise TNCConfigurationError("packet TXDELAY must be 0..255")
    if not 0 <= config.persist <= 255:
        raise TNCConfigurationError("packet.persist must be 0..255")
    if not 1 <= config.slottime <= 255:
        raise TNCConfigurationError("packet SLOTTIME must be 1..255")
    if not (config.kiss.enabled or config.agw.enabled):
        raise TNCConfigurationError("at least one of KISS or AGW must be enabled")
    if not config.agw_raw_only:
        raise TNCConfigurationError("initial AGW support is raw-only and requires agw.raw_only=true")
    if config.required_identity != QUALIFIED_FIRMWARE_IDENTITY:
        raise TNCConfigurationError("firmware.required_identity does not match the qualified AX25R4 image")
    if config.tx_enabled and (
        config.frequency_hz != QUALIFIED_TX_FREQUENCY_HZ
        or config.tx_power != QUALIFIED_TX_POWER
    ):
        raise TNCConfigurationError(
            "TX is initially restricted to the physically-qualified 145.050 MHz / power-200 profile"
        )


def load_config(path: str | Path) -> TNCConfig:
    path = Path(path)
    try:
        with path.open("rb") as handle:
            root = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise TNCConfigurationError(f"cannot load configuration {path}: {exc}") from exc

    hardware = _table(root, "hardware")
    radio = _table(root, "radio")
    packet = _table(root, "packet")
    kiss = _table(root, "kiss")
    agw = _table(root, "agw")
    firmware = _table(root, "firmware")

    baud = _int(packet, "baud")
    if baud != 1200:
        raise TNCConfigurationError("packet.baud must remain 1200")
    txdelay_ms = _int(packet, "txdelay_ms")
    slottime_ms = _int(packet, "slottime_ms")
    if txdelay_ms < 0 or txdelay_ms > 2550 or txdelay_ms % 10:
        raise TNCConfigurationError("packet.txdelay_ms must be 0..2550 in 10 ms increments")
    if slottime_ms < 10 or slottime_ms > 2550 or slottime_ms % 10:
        raise TNCConfigurationError("packet.slottime_ms must be 10..2550 in 10 ms increments")
    if _bool(firmware, "allow_automatic_flash"):
        raise TNCConfigurationError("automatic firmware flashing is not permitted")

    config = TNCConfig(
        target=_string(hardware, "target"),
        device=_string(radio, "device"),
        frequency_hz=_frequency_hz(radio.get("frequency_mhz")),
        tx_power=_int(radio, "tx_power"),
        tx_enabled=_bool(radio, "tx_enabled"),
        txdelay=txdelay_ms // 10,
        persist=_int(packet, "persist"),
        slottime=slottime_ms // 10,
        kiss=_listener(kiss, "kiss"),
        agw=_listener(agw, "agw"),
        agw_raw_only=_bool(agw, "raw_only"),
        required_identity=_string(firmware, "required_identity"),
    )
    validate_config(config)
    return config
