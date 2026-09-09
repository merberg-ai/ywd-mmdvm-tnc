#!/usr/bin/env bash
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
