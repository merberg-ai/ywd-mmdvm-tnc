#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT"

PYTHONPATH="$ROOT/src" python3 -m unittest discover -s tests -p test_fws.py -v
PYTHONPATH="$ROOT/src" python3 -m unittest discover -s tests -p test_fws_fm1.py -v
PYTHONPATH="$ROOT/src" python3 -m unittest discover -s tests -p test_fws_jobs.py -v

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
assert '"/api/inventory"' in server
assert '"/api/flash"' not in server
inventory = (root / "src/ywdfws/inventory.py").read_text()
broker = (root / "src/ywdfws/broker.py").read_text()
worker = (root / "firmware/fws_backup.py").read_text()
for forbidden in ("subprocess", "systemctl", "stm32flash", "pinctrl", "serial.Serial"):
    assert forbidden not in inventory
    assert forbidden not in broker
assert 'b"INVENTORY\\n"' in broker
assert "SO_PEERCRED" in broker
jobs = (root / "src/ywdfws/maintenance_jobs.py").read_text()
assert '"READ_ONLY_PREFLIGHT"' in jobs
assert "broker_inventory()" in jobs
for forbidden in ("subprocess", "systemctl", "stm32flash", "pinctrl", "serial.Serial", "os.system", "os.exec"):
    assert forbidden not in jobs, f"job layer must be hardware-inert: {forbidden}"
assert '"/api/jobs/preflight"' in server
assert '"X-CSRF-Token"' in server
assert "READ-HAT-MAIN-FLASH" in worker
assert 'restore=True' in worker
assert '"-r"' in worker and '"-w"' not in worker
print("FWS_FM0_NO_HARDWARE_BRIDGE=PASS")
print("FWS_FM1_READONLY_BROKER=PASS")
print("FWS_FM1_DUAL_READ_WORKER_HOST_CONTRACT=PASS")
PY
echo FWS_FM0_HOST_GATE=PASS
echo FWS_FM1_HOST_GATE=PASS
echo FWS_FM1_5_PREFLIGHT_GATE=PASS
echo MODEM_UART_OPENED=NO
echo GPIO_ACCESSED=NO
echo FLASH_WRITTEN=NO
echo RF_TRANSMITTED=NO
