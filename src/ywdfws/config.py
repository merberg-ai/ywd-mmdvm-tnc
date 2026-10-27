"""Fail-closed configuration for the on-demand firmware management UI."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import ipaddress
import tomllib


RFC1918_NETWORKS = tuple(ipaddress.IPv4Network(cidr) for cidr in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"
))


class StudioConfigError(ValueError):
    pass


@dataclass(frozen=True)
class StudioConfig:
    listen: str = "127.0.0.1"
    port: int = 8090
    allow_private_bind: bool = False
    password_file: Path = Path("~/.config/ywd-fws/credentials.json")
    session_minutes: int = 30
    tls_cert_file: Path | None = None
    tls_key_file: Path | None = None


def validate(config: StudioConfig) -> StudioConfig:
    try:
        address = ipaddress.ip_address(config.listen)
    except (TypeError, ValueError) as exc:
        raise StudioConfigError("listen must be a numeric IPv4 address") from exc
    if address.version != 4 or address.is_unspecified or address.is_multicast:
        raise StudioConfigError("listen must be a specific IPv4 loopback or RFC1918 address")
    if not isinstance(config.allow_private_bind, bool):
        raise StudioConfigError("allow_private_bind must be true or false")
    if not address.is_loopback:
        if not config.allow_private_bind:
            raise StudioConfigError("private LAN listening requires allow_private_bind=true")
        if not any(address in network for network in RFC1918_NETWORKS):
            raise StudioConfigError("only RFC1918 private LAN addresses are permitted")
    if isinstance(config.port, bool) or not isinstance(config.port, int) or not 1 <= config.port <= 65535:
        raise StudioConfigError("port must be 1..65535")
    if (isinstance(config.session_minutes, bool) or
            not isinstance(config.session_minutes, int) or
            not 5 <= config.session_minutes <= 240):
        raise StudioConfigError("session_minutes must be 5..240")
    if not isinstance(config.password_file, Path):
        raise StudioConfigError("password_file must be a path")
    if (config.tls_cert_file is None) != (config.tls_key_file is None):
        raise StudioConfigError("TLS certificate and private key must be supplied together")
    for key, value in (("tls.cert_file", config.tls_cert_file), ("tls.key_file", config.tls_key_file)):
        if value is not None and not isinstance(value, Path):
            raise StudioConfigError(f"{key} must be a file path")
    return config


def load(path: str | Path) -> StudioConfig:
    try:
        with Path(path).open("rb") as handle:
            raw = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise StudioConfigError(f"cannot load firmware studio configuration: {exc}") from exc
    if set(raw) - {"web", "auth", "tls"}:
        raise StudioConfigError("unknown configuration tables")
    web = raw.get("web", {})
    auth = raw.get("auth", {})
    tls = raw.get("tls", {})
    if not isinstance(web, dict) or not isinstance(auth, dict) or not isinstance(tls, dict):
        raise StudioConfigError("[web], [auth], and [tls] must be tables")
    if set(web) - {"listen", "port", "allow_private_bind"} or set(auth) - {"password_file", "session_minutes"}:
        raise StudioConfigError("unknown firmware studio configuration key")
    if set(tls) - {"cert_file", "key_file"}:
        raise StudioConfigError("unknown TLS configuration key")
    if any(not isinstance(v, str) for v in tls.values()):
        raise StudioConfigError("TLS path values must be strings")
    cfg = StudioConfig(
        listen=web.get("listen", "127.0.0.1"),
        port=web.get("port", 8090),
        allow_private_bind=web.get("allow_private_bind", False),
        password_file=Path(auth.get("password_file", "~/.config/ywd-fws/credentials.json")),
        session_minutes=auth.get("session_minutes", 30),
        tls_cert_file=Path(tls["cert_file"]).expanduser() if tls.get("cert_file") else None,
        tls_key_file=Path(tls["key_file"]).expanduser() if tls.get("key_file") else None,
    )
    return validate(cfg)
