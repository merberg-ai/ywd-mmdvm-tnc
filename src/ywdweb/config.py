"""Configuration for the passive YWD packet-monitor WebUI."""
from __future__ import annotations

from dataclasses import dataclass
import ipaddress
from pathlib import Path
import tomllib


class WebUIConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class WebConfig:
    listen: str = "127.0.0.1"
    port: int = 8088
    allow_wildcard_bind: bool = False


@dataclass(frozen=True)
class MonitorSourceConfig:
    host: str = "127.0.0.1"
    port: int = 8002
    reconnect_seconds: float = 2.0


@dataclass(frozen=True)
class HistoryConfig:
    directory: Path = Path("/var/log/ywd-packetlog")
    recent_capacity: int = 512
    sse_queue_capacity: int = 256
    max_history_limit: int = 1000
    max_console_lines: int = 2000
    max_stats_events: int = 100000


@dataclass(frozen=True)
class WebUIConfig:
    web: WebConfig = WebConfig()
    monitor: MonitorSourceConfig = MonitorSourceConfig()
    history: HistoryConfig = HistoryConfig()


def _table(root: dict, name: str) -> dict:
    value = root.get(name, {})
    if not isinstance(value, dict):
        raise WebUIConfigurationError(f"[{name}] must be a table")
    return value


def _bool(table: dict, key: str, default: bool) -> bool:
    value = table.get(key, default)
    if not isinstance(value, bool):
        raise WebUIConfigurationError(f"{key} must be true or false")
    return value


def _int(table: dict, key: str, default: int) -> int:
    value = table.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise WebUIConfigurationError(f"{key} must be an integer")
    return int(value)


def _float(table: dict, key: str, default: float) -> float:
    value = table.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WebUIConfigurationError(f"{key} must be numeric")
    return float(value)


def _string(table: dict, key: str, default: str) -> str:
    value = table.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise WebUIConfigurationError(f"{key} must be a non-empty string")
    return value.strip()


def _validate_web_bind(host: str, allow_wildcard: bool) -> None:
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise WebUIConfigurationError("web.listen must be an IPv4 address") from exc
    if address.version != 4:
        raise WebUIConfigurationError("web.listen must be IPv4")
    if address.is_unspecified:
        if host != "0.0.0.0" or not allow_wildcard:
            raise WebUIConfigurationError(
                "web.listen=0.0.0.0 requires web.allow_wildcard_bind=true"
            )
        return
    if not (address.is_loopback or address.is_private):
        raise WebUIConfigurationError("web.listen must be loopback/private; public binds are rejected")


def _validate_monitor_host(host: str) -> None:
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise WebUIConfigurationError("monitor.host must be an IPv4 loopback address") from exc
    if address.version != 4 or not address.is_loopback:
        raise WebUIConfigurationError(
            "monitor.host must remain loopback-only so the WebUI observes the local passive monitor"
        )


def validate_config(config: WebUIConfig) -> WebUIConfig:
    _validate_web_bind(config.web.listen, config.web.allow_wildcard_bind)
    _validate_monitor_host(config.monitor.host)
    if not 1 <= config.web.port <= 65535:
        raise WebUIConfigurationError("web.port must be 1..65535")
    if not 1 <= config.monitor.port <= 65535:
        raise WebUIConfigurationError("monitor.port must be 1..65535")
    if config.monitor.reconnect_seconds <= 0:
        raise WebUIConfigurationError("monitor.reconnect_seconds must be positive")
    if config.history.recent_capacity < 16:
        raise WebUIConfigurationError("history.recent_capacity must be at least 16")
    if config.history.sse_queue_capacity < 8:
        raise WebUIConfigurationError("history.sse_queue_capacity must be at least 8")
    if not 1 <= config.history.max_history_limit <= 10000:
        raise WebUIConfigurationError("history.max_history_limit must be 1..10000")
    if not 1 <= config.history.max_console_lines <= 20000:
        raise WebUIConfigurationError("history.max_console_lines must be 1..20000")
    if not 100 <= config.history.max_stats_events <= 1000000:
        raise WebUIConfigurationError("history.max_stats_events must be 100..1000000")
    return config


def load_config(path: str | Path) -> WebUIConfig:
    path = Path(path)
    try:
        with path.open("rb") as handle:
            root = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise WebUIConfigurationError(f"cannot load WebUI configuration {path}: {exc}") from exc

    web = _table(root, "web")
    monitor = _table(root, "monitor")
    history = _table(root, "history")
    config = WebUIConfig(
        web=WebConfig(
            listen=_string(web, "listen", "127.0.0.1"),
            port=_int(web, "port", 8088),
            allow_wildcard_bind=_bool(web, "allow_wildcard_bind", False),
        ),
        monitor=MonitorSourceConfig(
            host=_string(monitor, "host", "127.0.0.1"),
            port=_int(monitor, "port", 8002),
            reconnect_seconds=_float(monitor, "reconnect_seconds", 2.0),
        ),
        history=HistoryConfig(
            directory=Path(_string(history, "directory", "/var/log/ywd-packetlog")),
            recent_capacity=_int(history, "recent_capacity", 512),
            sse_queue_capacity=_int(history, "sse_queue_capacity", 256),
            max_history_limit=_int(history, "max_history_limit", 1000),
            max_console_lines=_int(history, "max_console_lines", 2000),
            max_stats_events=_int(history, "max_stats_events", 100000),
        ),
    )
    return validate_config(config)
