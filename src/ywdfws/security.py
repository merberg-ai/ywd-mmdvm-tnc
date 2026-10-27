"""Local password-file authentication for an on-demand loopback-only service."""
from __future__ import annotations

import getpass
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import stat


class SecurityError(ValueError):
    pass


def _derive(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=16384, r=8, p=1, dklen=32)


def create_password_file(path: Path, password: str) -> None:
    if len(password) < 12 or len(password) > 256:
        raise SecurityError("password must contain 12 to 256 characters")
    target = path.expanduser()
    target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    salt = secrets.token_bytes(16)
    payload = {"schema": 1, "kdf": "scrypt-16384-8-1", "salt": salt.hex(),
               "hash": _derive(password, salt).hex()}
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f)
            f.write("\n")
    except BaseException:
        target.unlink(missing_ok=True)
        raise


def initialize_interactive(path: Path) -> None:
    first = getpass.getpass("Firmware Studio password (minimum 12 characters): ")
    second = getpass.getpass("Confirm password: ")
    if not hmac.compare_digest(first, second):
        raise SecurityError("passwords did not match")
    create_password_file(path, first)


class PasswordVerifier:
    def __init__(self, path: Path):
        path = path.expanduser()
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise SecurityError("password file must be regular, owned by the service user, and mode 0600")
        data = json.loads(path.read_text(encoding="utf-8"))
        if set(data) != {"schema", "kdf", "salt", "hash"} or data["schema"] != 1 or data["kdf"] != "scrypt-16384-8-1":
            raise SecurityError("unsupported credentials file")
        self.salt = bytes.fromhex(data["salt"])
        self.expected = bytes.fromhex(data["hash"])
        if len(self.salt) != 16 or len(self.expected) != 32:
            raise SecurityError("invalid password verifier")

    def verify(self, password: str) -> bool:
        if not isinstance(password, str) or len(password) > 256:
            return False
        return hmac.compare_digest(_derive(password, self.salt), self.expected)
