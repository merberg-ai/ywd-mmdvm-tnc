"""Fail-closed configuration for the on-demand firmware management UI."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import ipaddress
import tomllib


class StudioConfigError(ValueError):
    pass


@dataclass(frozen=True)
class StudioConfig:
    listen: str = "127.0.0.1"
    port: int = 8090
    password_file: Path = Path("~/.config/ywd-fws/credentials.json")
    session_minutes: int = 30


def validate(config: StudioConfig) -> StudioConfig:
    try:
        address = ipaddress.ip_address(config.listen)
    except ValueError as exc:
        raise StudioConfigError("listen must be an IPv4 loopback address") from exc
    if address.version != 4 or not address.is_loopback:
        raise StudioConfigError("FM0 accepts loopback listeners only; use an SSH tunnel for remote access")
    if isinstance(config.port, bool) or not isinstance(config.port, int) or not 1 <= config.port <= 65535:
        raise StudioConfigError("port must be 1..65535")
    if (isinstance(config.session_minutes, bool) or
            not isinstance(config.session_minutes, int) or
            not 5 <= config.session_minutes <= 240):
        raise StudioConfigError("session_minutes must be 5..240")
    if not isinstance(config.password_file, Path):
        raise StudioConfigError("password_file must be a path")
    return config


def load(path: str | Path) -> StudioConfig:
    try:
        with Path(path).open("rb") as handle:
            raw = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise StudioConfigError(f"cannot load firmware studio configuration: {exc}") from exc
    if set(raw) - {"web", "auth"}:
        raise StudioConfigError("unknown configuration tables")
    web = raw.get("web", {})
    auth = raw.get("auth", {})
    if not isinstance(web, dict) or not isinstance(auth, dict):
        raise StudioConfigError("[web] and [auth] must be tables")
    if set(web) - {"listen", "port"} or set(auth) - {"password_file", "session_minutes"}:
        raise StudioConfigError("unknown firmware studio configuration key")
    cfg = StudioConfig(
        listen=web.get("listen", "127.0.0.1"),
        port=web.get("port", 8090),
        password_file=Path(auth.get("password_file", "~/.config/ywd-fws/credentials.json")),
        session_minutes=auth.get("session_minutes", 30),
    )
    return validate(cfg)
