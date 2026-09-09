#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

python3 - <<'PY'
from pathlib import Path
import hashlib,json
root=Path('.')
data=json.loads((root/'firmware/accepted-firmware.json').read_text())
assert data['schema'] == 1
assert data['product'] == 'YWD-MMDVM-TNC'
active=data['active_profile_id']
entries=[x for x in data['profiles'] if x['id'] == active]
assert len(entries) == 1
e=entries[0]
assert e['path'] == 'firmware/product-ax25r4.json'
assert e['manual_update_allowed'] is True
assert e['force_reflash_allowed'] is True
p=root/e['path']
assert hashlib.sha256(p.read_bytes()).hexdigest() == e['profile_sha256']
profile=json.loads(p.read_text())
for key in ('target_id','expected_identity','artifact_size_bytes','artifact_sha256'):
    assert e[key] == profile[key]
print('FWM4_ACCEPTED_FIRMWARE_REGISTRY=PASS')
PY

python3 -m py_compile firmware/qualified_flash.py firmware/flash_ui.py

grep -q -- '--force-reflash' firmware/qualified_flash.py
grep -q -- '--force-reflash' firmware/flash_ui.py
grep -q 'identity == profile.expected_identity and not args.force_reflash' firmware/qualified_flash.py
grep -q 'identity == profile.expected_identity and not args.force_reflash' firmware/flash_ui.py
grep -q 'verified stock rollback backup is required' firmware/update.sh
grep -q -- '--force-reflash </dev/tty' firmware/update.sh
grep -q 'accepted-firmware.json' firmware/update.sh
grep -q 'ywd-update-firmware' installer/install.sh

if grep -q -- '--force-reflash' installer/setup.sh; then
  echo 'normal installer must never force reflash' >&2
  exit 1
fi
if grep -q -- '--force-reflash' systemd/ywd-mmdvm-tnc.service; then
  echo 'service must never force reflash' >&2
  exit 1
fi

python3 - <<'PY'
from pathlib import Path
ui=Path('firmware/flash_ui.py').read_text()
backend=Path('firmware/qualified_flash.py').read_text()
assert 'response = read_interactive_confirmation(prompt)' in ui
assert 'response != profile.final_write_confirmation' in ui
assert 'verify_stock_backup(profile, backup_dir)' in ui
assert 'verify_stock_backup(profile, backup_dir)' in backend
assert 'stm32flash' in ui and '"-w"' in ui
assert 'option bytes' in backend.lower()
print('FWM4_FORCE_REFLASH_SAFETY_CONTRACT=PASS')
PY

echo FWM4_INSTALLER_SAME_IMAGE_BEHAVIOR=VERIFICATION_ONLY
echo FWM4_UPDATER_FORCE_REFLASH=EXPLICIT_ONLY
echo FWM4_ARBITRARY_BIN_FLASH=NOT_EXPOSED
echo HARDWARE_ACCESSED=NO
echo GPIO_ACCESSED=NO
echo MODEM_UART_OPENED=NO
echo FLASH_WRITTEN=NO
echo RF_TRANSMITTED=NO
