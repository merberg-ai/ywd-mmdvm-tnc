#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

bash "$ROOT/scripts/check-p3-lan-kiss.sh"
bash "$ROOT/scripts/check-firmware-inrepo-migration.sh"
bash "$ROOT/scripts/check-runtime-inrepo-migration.sh"

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
python3 -m py_compile "$ROOT/firmware/flash_ui.py" "$ROOT/firmware/hat_control.py" "$ROOT/firmware/probe_hat.py" "$ROOT/firmware/build-qualified-inrepo.py"

python3 - "$ROOT" <<'PY'
from pathlib import Path
import json
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
profile = json.loads((root / "firmware/product-ax25r4.json").read_text(encoding="utf-8"))
with (root / "config/ywd-mmdvm-tnc.example.toml").open("rb") as fh:
    cfg = tomllib.load(fh)
with (root / "qualification/public-stock-hat-install-physical-2026-09-07.json").open(encoding="utf-8") as fh:
    stock_hat = json.load(fh)

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
assert 'BUILDER="$ROOT/firmware/build-qualified-inrepo.py"' in build
assert 'TOOLCHAIN="$ROOT/firmware/tooling/qualified-toolchain.json"' in build
assert 'firmware/build-qualified-inrepo.py' in build
assert 'firmware/tooling' in build
assert 'firmware/vendor' in build
assert 'sudo -H -u "$build_user" -- python3 "$build_root/firmware/build-qualified-inrepo.py"' in build
assert 'chown -R "$build_user:$build_group" "$BUILD_WORKSPACE"' in build
assert 'FIRMWARE_BUILD_SOURCE=IN_REPO' in build
assert 'YWD1278_FIRMWARE_BUILDER_INVOKED=NO' in build
assert 'build-packet-rssi-ywd1278.py' not in build
assert 'BUILD_EXECUTED_AS_ROOT=NO' in build
assert '$2/vendor/ywd-1278' not in (root / "installer/install.sh").read_text(encoding="utf-8")
assert 'vendor/ywd-1278/firmware' not in (root / "firmware/probe.sh").read_text(encoding="utf-8")
assert 'ROOT / "vendor" / "ywd-1278"' not in (root / "firmware/qualified_flash.py").read_text(encoding="utf-8")
assert 'def read_interactive_confirmation' in flash_ui
assert 'sys.stdin.isatty()' in flash_ui
assert 'open("/dev/tty", "r+"' in flash_ui
assert 'response = read_interactive_confirmation(prompt)' in flash_ui
assert 'systemctl enable "$SERVICE"' in setup
assert 'systemctl restart "$SERVICE"' in setup

assert profile["vendor_build_script"] == "firmware/build-qualified-inrepo.py"
assert profile["artifact_relative_path"].startswith("firmware/out/0c-p2-rssi-ax25r4-")
assert profile["artifact_size_bytes"] == 59892
assert profile["artifact_sha256"] == "b06fcbf0baa36e865198091cee27c66e1624ef08117ee685253a7a5613c7c616"
assert profile["firmware_engineering_manifest"] == "firmware/tooling/packet-rssi-build-manifest.json"
assert profile["qualified_toolchain_manifest"] == "firmware/tooling/qualified-toolchain.json"

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
assert 'public-stock-hat-install-physical-2026-09-07.json' in readme
assert 'firmware/build-qualified-inrepo.py' in readme
assert 'firmware/tooling/qualified-toolchain.json' in readme
assert 'Testing the development branch' not in readme
assert 'YWD_TNC_REF=dev' not in readme
assert 'git clone --recursive -b dev' not in readme

assert stock_hat["status"] == "PASS"
assert stock_hat["product"] == "YWD-MMDVM-TNC"
assert stock_hat["code_under_test_commit"] == "ec73ff79184bcb8d3880a60ed54f1398eabc154c"
assert stock_hat["product_version"] == "0.1.0a9"
assert stock_hat["firmware_build"]["reproducible_builds"] == "PASS"
assert stock_hat["stock_backup"]["golden_stock_sha256_match"] is True
assert stock_hat["stock_backup"]["flash_written_before_final_confirmation"] is False
assert stock_hat["operator_reported_completion"]["installer_completed_successfully"] == "PASS"
assert stock_hat["operator_reported_completion"]["linbpq_receive_over_kiss"] == "PASS"
assert stock_hat["operator_reported_completion"]["linbpq_transmit_over_kiss"] == "PASS"

assert 'ywd-1278.service' not in bootstrap
assert 'ywd-1278.service' not in setup
assert 'ywd-1278.service' in service

print("PUBLIC_INSTALLER_UI_CONTRACT=PASS")
print("PUBLIC_STOCK_HAT_AUTO_BUILD_CONTRACT=PASS")
print("PUBLIC_NONROOT_FIRMWARE_BUILD_CONTRACT=PASS")
print("PUBLIC_INREPO_FIRMWARE_BUILD_CONTRACT=PASS")
print("PUBLIC_SCRIPT_EXECUTION_CONTRACT=PASS")
print("PUBLIC_TTY_FLASH_CONFIRMATION_CONTRACT=PASS")
print("PUBLIC_CONFIG_SAFE_DEFAULTS=PASS")
print("PUBLIC_SERVICE_BRANDING=PASS")
print("PUBLIC_README_CONTRACT=PASS")
print("PUBLIC_STOCK_HAT_PHYSICAL_EVIDENCE_CONTRACT=PASS")
print("FWM2_PHYSICAL_FLASH_PERFORMED=NO")
print("RF_RUNTIME_BEHAVIOR_CHANGED=NO")
PY

echo "YWD_TNC_PUBLIC_POLISH_HOST_CONTRACT=PASS"
echo "HARDWARE_ACCESSED=NO"
echo "RF_TRANSMITTED=NO"
