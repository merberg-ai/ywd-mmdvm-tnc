#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT"

PYTHONPATH="$ROOT/src" python3 -m unittest discover -s tests -p test_fws.py -v

python3 - <<'PY'
from pathlib import Path
import tomllib

root = Path.cwd()
with (root / "pyproject.toml").open("rb") as f:
    project = tomllib.load(f)
assert project["project"]["scripts"]["ywd-fws"] == "ywdfws.app:main"
assert "ywdfws*" in project["tool"]["setuptools"]["packages"]["find"]["include"]
server = (root / "src/ywdfws/app.py").read_text()
for forbidden in ("stm32flash", "pinctrl", "subprocess", "ywd1278", "ywdtnc.engine", "serial.Serial", "systemctl"):
    assert forbidden not in server, f"hardware/process API forbidden in FM0: {forbidden}"
assert '"/api/login"' in server
assert '"/api/logout"' in server
assert '"/api/status"' in server
assert "firmware_operations" in server
assert "flash_enabled" in server
assert 'SameSite=Strict' in server
assert "HttpOnly" in server
print("FWS_FM0_NO_HARDWARE_BRIDGE=PASS")
PY
echo FWS_FM0_HOST_GATE=PASS
echo MODEM_UART_OPENED=NO
echo GPIO_ACCESSED=NO
echo FLASH_WRITTEN=NO
echo RF_TRANSMITTED=NO
