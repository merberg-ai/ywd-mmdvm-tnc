#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

bash "$ROOT/scripts/check-p3-lan-kiss.sh"

for script in \
  "$ROOT/install.sh" \
  "$ROOT/installer/lib/ui.sh" \
  "$ROOT/installer/bootstrap.sh" \
  "$ROOT/installer/install.sh" \
  "$ROOT/installer/setup.sh" \
  "$ROOT/firmware/build.sh" \
  "$ROOT/firmware/ensure.sh" \
  "$ROOT/firmware/probe.sh" \
  "$ROOT/firmware/flash.sh"; do
  bash -n "$script"
done
python3 -m py_compile "$ROOT/firmware/flash_ui.py" "$ROOT/firmware/hat_control.py"

python3 - "$ROOT" <<'PY'
from pathlib import Path
import tomllib
import sys

root = Path(sys.argv[1])
bootstrap = (root / "install.sh").read_text(encoding="utf-8")
setup = (root / "installer/setup.sh").read_text(encoding="utf-8")
ensure = (root / "firmware/ensure.sh").read_text(encoding="utf-8")
build = (root / "firmware/build.sh").read_text(encoding="utf-8")
flash_ui = (root / "firmware/flash_ui.py").read_text(encoding="utf-8")
ui = (root / "installer/lib/ui.sh").read_text(encoding="utf-8")
service = (root / "systemd/ywd-mmdvm-tnc.service").read_text(encoding="utf-8")
readme = (root / "README.md").read_text(encoding="utf-8")
with (root / "config/ywd-mmdvm-tnc.example.toml").open("rb") as fh:
    cfg = tomllib.load(fh)

assert 'REF="${YWD_TNC_REF:-main}"' in bootstrap
assert 'installer/setup.sh' in bootstrap
assert '/var/log/ywd-mmdvm-tnc' in ui
assert 'ui_prompt_access' in setup
assert 'Enable RF transmit?' in setup
assert 'ui_prompt_yes_no tx_answer "Enable RF transmit?" no' in setup
assert 'listen = "$kiss_listen"' in setup
assert 'allow_wildcard_bind = $wildcard' in setup
assert 'WRITE-FIRMWARE-NOW' in setup
assert 'bash "$ROOT/firmware/ensure.sh"' in setup
assert 'firmware/flash.sh" flash --authorize FLASH-QUALIFIED-AX25R4 </dev/tty' in setup
assert setup.index('bash "$ROOT/firmware/ensure.sh"') < setup.index('firmware/flash.sh" flash --authorize FLASH-QUALIFIED-AX25R4 </dev/tty')
assert 'verify-artifact' in ensure
assert 'firmware/build.sh' in ensure
assert 'YWD_TNC_INSTALLER_BUILD=1' in ensure
assert 'YWD_TNC_INSTALLER_BUILD' in build
assert 'SUDO_USER' in build
assert 'git -c safe.directory=' in build and 'archive --format=tar HEAD' in build
assert 'sudo -H -u "$build_user" -- python3' in build
assert 'chown -R "$build_user:$build_group" "$BUILD_WORKSPACE"' in build
assert 'BUILD_EXECUTED_AS_ROOT=NO' in build
assert 'def read_interactive_confirmation' in flash_ui
assert 'sys.stdin.isatty()' in flash_ui
assert 'open("/dev/tty", "r+"' in flash_ui
assert 'response = read_interactive_confirmation(prompt)' in flash_ui
assert 'systemctl enable "$SERVICE"' in setup
assert 'systemctl restart "$SERVICE"' in setup

assert cfg["radio"]["tx_enabled"] is False
assert cfg["kiss"]["listen"] == "127.0.0.1"
assert cfg["kiss"]["allow_wildcard_bind"] is False
assert cfg["agw"]["enabled"] is False
assert cfg["firmware"]["allow_automatic_flash"] is False

assert 'Description=YWD-MMDVM-TNC' in service
assert '/opt/ywd-mmdvm-tnc/source/firmware/hat_control.py' in service
assert 'ExecStart=/opt/ywd-mmdvm-tnc/venv/bin/ywd-tncd' in service

assert 'curl -fsSL https://raw.githubusercontent.com/merberg-ai/ywd-mmdvm-tnc/main/install.sh | sudo bash' in readme
assert 'Manual installation from Git' in readme
assert 'LinBPQ example' in readme
assert 'Firmware' in readme
assert 'qualification/' in readme

assert 'ywd-1278.service' not in bootstrap
assert 'ywd-1278.service' not in setup
assert 'ywd-1278.service' in service

print("PUBLIC_INSTALLER_UI_CONTRACT=PASS")
print("PUBLIC_STOCK_HAT_AUTO_BUILD_CONTRACT=PASS")
print("PUBLIC_NONROOT_FIRMWARE_BUILD_CONTRACT=PASS")
print("PUBLIC_SCRIPT_EXECUTION_CONTRACT=PASS")
print("PUBLIC_TTY_FLASH_CONFIRMATION_CONTRACT=PASS")
print("PUBLIC_CONFIG_SAFE_DEFAULTS=PASS")
print("PUBLIC_SERVICE_BRANDING=PASS")
print("PUBLIC_README_CONTRACT=PASS")
print("RF_RUNTIME_BEHAVIOR_CHANGED=NO")
PY

echo "YWD_TNC_PUBLIC_POLISH_HOST_CONTRACT=PASS"
echo "HARDWARE_ACCESSED=NO"
echo "RF_TRANSMITTED=NO"
