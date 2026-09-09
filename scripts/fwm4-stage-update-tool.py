#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, data: str, mode: int | None = None) -> None:
    p = ROOT / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(data, encoding="utf-8")
    if mode is not None:
        p.chmod(mode)


def replace_once(path: str, old: str, new: str) -> None:
    data = text(path)
    if old not in data:
        if new in data:
            return
        raise SystemExit(f"missing replacement anchor in {path}: {old[:80]!r}")
    if data.count(old) != 1:
        raise SystemExit(f"replacement anchor not unique in {path}: {old[:80]!r}")
    write(path, data.replace(old, new, 1))


# Version bump for the isolated updater phase.
replace_once("pyproject.toml", 'version = "0.1.0a11"', 'version = "0.1.0a12"')
replace_once("src/ywdtnc/__init__.py", '__version__ = "0.1.0a11"', '__version__ = "0.1.0a12"')

# Backend: add an explicit force-reflash flag. Installer behavior remains unchanged
# because it never passes this flag.
replace_once(
    "firmware/qualified_flash.py",
    '    parser.add_argument("--authorize", default="")\n    args = parser.parse_args()\n',
    '    parser.add_argument("--authorize", default="")\n'
    '    parser.add_argument("--force-reflash", action="store_true")\n'
    '    args = parser.parse_args()\n',
)
replace_once(
    "firmware/qualified_flash.py",
    '        require_root()\n        require_tools()\n        profile = load_profile(PROFILE_PATH)\n',
    '        require_root()\n        require_tools()\n'
    '        if args.force_reflash and args.mode != "flash":\n'
    '            raise FlashError("--force-reflash is valid only with flash mode")\n'
    '        profile = load_profile(PROFILE_PATH)\n',
)
replace_once(
    "firmware/qualified_flash.py",
    '        print("OPTION_BYTES_PERMITTED=NO")\n',
    '        print("OPTION_BYTES_PERMITTED=NO")\n'
    '        print(f"FORCE_REFLASH={\'YES\' if args.force_reflash else \'NO\'}")\n',
)
replace_once(
    "firmware/qualified_flash.py",
    '                if identity == profile.expected_identity:\n',
    '                if identity == profile.expected_identity and not args.force_reflash:\n',
)
replace_once(
    "firmware/qualified_flash.py",
    '            print(f"FLASH_WRITTEN={\'YES\' if flash_written else \'NO\'}")\n',
    '            print(f"FLASH_WRITTEN={\'YES\' if flash_written else \'NO\'}")\n'
    '            print(f"FORCE_REFLASH={\'YES\' if args.force_reflash else \'NO\'}")\n',
)

# Public UI: same explicit flag and same fail-closed semantics.
replace_once(
    "firmware/flash_ui.py",
    '    parser.add_argument("--authorize", default="")\n    args = parser.parse_args()\n',
    '    parser.add_argument("--authorize", default="")\n'
    '    parser.add_argument("--force-reflash", action="store_true")\n'
    '    args = parser.parse_args()\n',
)
replace_once(
    "firmware/flash_ui.py",
    '        qf.require_root()\n        qf.require_tools()\n        profile = qf.load_profile(qf.PROFILE_PATH)\n',
    '        qf.require_root()\n        qf.require_tools()\n'
    '        if args.force_reflash and args.mode != "flash":\n'
    '            raise qf.FlashError("--force-reflash is valid only with flash mode")\n'
    '        profile = qf.load_profile(qf.PROFILE_PATH)\n',
)
replace_once(
    "firmware/flash_ui.py",
    '                if identity == profile.expected_identity:\n',
    '                if identity == profile.expected_identity and not args.force_reflash:\n',
)
replace_once(
    "firmware/flash_ui.py",
    '                    qf.verify_stock_backup(profile, backup_dir)\n                    print()\n                    warn("A firmware write is ready. The verified stock backup is safe.")\n',
    '                    qf.verify_stock_backup(profile, backup_dir)\n'
    '                    print()\n'
    '                    if args.force_reflash and identity == profile.expected_identity:\n'
    '                        warn("A forced reflash of the currently accepted firmware was explicitly requested.")\n'
    '                    warn("A firmware write is ready. The verified stock backup is safe.")\n',
)
replace_once(
    "firmware/flash_ui.py",
    '            print(f"  flash written: {\'yes\' if flash_written else \'no (verification only)\'}")\n',
    '            print(f"  flash written: {\'yes\' if flash_written else \'no (verification only)\'}")\n'
    '            print(f"  force reflash requested: {\'yes\' if args.force_reflash else \'no\'}")\n',
)

# Accepted firmware registry: only repository-reviewed profiles can be selected by
# the updater. The exact profile SHA256 is pinned here.
profile_path = ROOT / "firmware/product-ax25r4.json"
profile = json.loads(profile_path.read_text(encoding="utf-8"))
profile_sha = hashlib.sha256(profile_path.read_bytes()).hexdigest()
accepted = {
    "schema": 1,
    "product": "YWD-MMDVM-TNC",
    "active_profile_id": "ax25r4-qualified",
    "profiles": [
        {
            "id": "ax25r4-qualified",
            "path": "firmware/product-ax25r4.json",
            "profile_sha256": profile_sha,
            "target_id": profile["target_id"],
            "expected_identity": profile["expected_identity"],
            "artifact_size_bytes": profile["artifact_size_bytes"],
            "artifact_sha256": profile["artifact_sha256"],
            "manual_update_allowed": True,
            "force_reflash_allowed": True,
        }
    ],
}
write("firmware/accepted-firmware.json", json.dumps(accepted, indent=2, sort_keys=True) + "\n")

update_script = r'''#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/installer/lib/ui.sh"
YWD_TNC_LOG_BASENAME=${YWD_TNC_LOG_BASENAME:-firmware-update}
ui_init "$@"

MANIFEST="$ROOT/firmware/accepted-firmware.json"
SERVICE="ywd-mmdvm-tnc.service"
DEVICE="${YWD_TNC_DEVICE:-/dev/ttyAMA0}"
service_was_active=no
update_succeeded=no

restore_service() {
  if [[ "$service_was_active" == yes && "$update_succeeded" != yes ]]; then
    systemctl start "$SERVICE" >/dev/null 2>&1 || true
  fi
}
trap restore_service EXIT

[[ ${EUID:-$(id -u)} -eq 0 ]] || { ui_fail "Run firmware update with sudo/root."; exit 2; }
[[ -f "$MANIFEST" ]] || { ui_fail "Accepted firmware manifest is missing."; exit 2; }
[[ -e "$DEVICE" ]] || { ui_fail "Modem UART does not exist: $DEVICE"; exit 2; }

readarray -t accepted < <(python3 - "$ROOT" "$MANIFEST" <<'PY'
from pathlib import Path
import hashlib,json,sys
root=Path(sys.argv[1]).resolve()
data=json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))
if data.get('schema') != 1 or data.get('product') != 'YWD-MMDVM-TNC':
    raise SystemExit('unsupported accepted firmware manifest')
active=data.get('active_profile_id')
entries=[x for x in data.get('profiles',[]) if x.get('id') == active]
if len(entries) != 1:
    raise SystemExit('active accepted firmware profile is missing or ambiguous')
e=entries[0]
if e.get('manual_update_allowed') is not True or e.get('force_reflash_allowed') is not True:
    raise SystemExit('active firmware profile is not approved for manual reflash')
rel=e.get('path')
if rel != 'firmware/product-ax25r4.json':
    raise SystemExit('active firmware profile path is not allowlisted')
p=(root/rel).resolve()
if root not in p.parents or not p.is_file():
    raise SystemExit('accepted firmware profile path is invalid')
raw=p.read_bytes()
if hashlib.sha256(raw).hexdigest() != e.get('profile_sha256'):
    raise SystemExit('accepted firmware profile digest mismatch')
profile=json.loads(raw)
for key in ('target_id','expected_identity','artifact_size_bytes','artifact_sha256'):
    if profile.get(key) != e.get(key):
        raise SystemExit(f'accepted firmware registry mismatch: {key}')
print(str(p))
print(str(profile['flash_authorization_token']))
print(str(profile['final_write_confirmation']))
print(str(profile['expected_identity']))
print(str(profile['artifact_sha256']))
PY
)
PROFILE="${accepted[0]}"
AUTHORIZE="${accepted[1]}"
CONFIRM="${accepted[2]}"
EXPECTED_IDENTITY="${accepted[3]}"
EXPECTED_SHA="${accepted[4]}"

ui_header "YWD-MMDVM-TNC firmware update"
ui_ok "Active accepted firmware profile verified"
printf '  profile: %s\n' "$PROFILE"
printf '  expected runtime: %s\n' "$EXPECTED_IDENTITY"
printf '  expected SHA-256: %s\n' "$EXPECTED_SHA"
printf '\n'
ui_warn "This command intentionally rewrites main flash even when this exact accepted firmware is already installed."
printf 'The normal installer remains verification-only when the exact firmware is already present.\n'
printf 'A verified stock rollback backup is required and the final write still requires typing %s.\n' "$CONFIRM"

if systemctl is-active --quiet "$SERVICE"; then
  service_was_active=yes
fi

YWD_TNC_LOG_FILE="$YWD_TNC_LOG_FILE" bash "$ROOT/firmware/ensure.sh"

[[ -r /dev/tty ]] || { ui_fail "Interactive firmware confirmation requires a controlling terminal (/dev/tty)."; exit 20; }

YWD_TNC_LOG_FILE="$YWD_TNC_LOG_FILE" \
  "$ROOT/firmware/flash.sh" flash \
  --device "$DEVICE" \
  --authorize "$AUTHORIZE" \
  --force-reflash </dev/tty

if [[ "$service_was_active" == yes ]]; then
  ui_run "Restarting YWD-MMDVM-TNC" systemctl restart "$SERVICE"
  systemctl is-active --quiet "$SERVICE" || { ui_fail "Service did not remain active after firmware update."; exit 30; }
  ui_ok "YWD-MMDVM-TNC is running"
fi

update_succeeded=yes
trap - EXIT
ui_header "Firmware update complete"
printf 'accepted_profile=%s\n' "$PROFILE"
printf 'artifact_sha256=%s\n' "$EXPECTED_SHA"
printf 'forced_reflash=yes\n'
printf 'service_restored=%s\n' "$service_was_active"
printf 'log=%s\n' "$YWD_TNC_LOG_FILE"
'''
write("firmware/update.sh", update_script, 0o755)

root_wrapper = '''#!/usr/bin/env bash\nset -Eeuo pipefail\nROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"\nexec "$ROOT/firmware/update.sh" "$@"\n'''
write("update-firmware.sh", root_wrapper, 0o755)

# Installed convenience command.
replace_once(
    "installer/install.sh",
    'ln -sfn "$VENV/bin/ywd-tnc-fw" /usr/local/bin/ywd-tnc-fw\n',
    'ln -sfn "$VENV/bin/ywd-tnc-fw" /usr/local/bin/ywd-tnc-fw\n'
    'ln -sfn "$SOURCE/update-firmware.sh" /usr/local/bin/ywd-update-firmware\n',
)

contract = r'''#!/usr/bin/env bash
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
'''
write("scripts/check-firmware-update-tooling.sh", contract, 0o755)

# README: concise public updater section.
readme = text("README.md")
marker = "## Firmware\n"
section = '''## Firmware updates and explicit reflashing\n\nThe normal installer is intentionally conservative: if the exact accepted firmware is already running, it verifies programmed bytes instead of rewriting flash. For an explicit firmware update or a deliberate reflash of the active accepted image, use:\n\n```bash\nsudo ywd-update-firmware\n```\n\nThe updater does **not** accept an arbitrary `.bin`. It loads the repository-owned `firmware/accepted-firmware.json` registry, verifies the exact active profile and artifact, requires a verified stock rollback backup, and still requires the interactive `WRITE-FIRMWARE-NOW` confirmation before main flash is written. Programmed bytes are read back and verified before the HAT is restarted. This is also the intended path for future qualified firmware revisions.\n\n'''
if section not in readme:
    if marker not in readme:
        raise SystemExit("README firmware marker not found")
    readme = readme.replace(marker, section + marker, 1)
    write("README.md", readme)

print("FWM4_STAGE=PASS")
print("INSTALLER_FORCE_REFLASH_CHANGED=NO")
print("HARDWARE_ACCESSED=NO GPIO_ACCESSED=NO UART_OPENED=NO FLASH_WRITTEN=NO RF_TRANSMITTED=NO")
