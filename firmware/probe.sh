#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/installer/lib/ui.sh"
YWD_TNC_LOG_BASENAME=firmware-probe
ui_init "$@"

DEVICE="${YWD_TNC_DEVICE:-/dev/ttyAMA0}"
TARGETS="$ROOT/vendor/ywd-1278/firmware/targets.json"
PROBE="$ROOT/vendor/ywd-1278/firmware/probe_hat.py"
EXPECTED_IDENTITY="MMDVM_HS_Hat-YWD-1278-AX25R4-v0.1.0-alpha1 14.7456MHz ADF7021 FW based on CA6JAU GitID #7ff74ed"
EXPECTED_TARGET="mmdvm-hs-hat-stm32f103-simplex-14.7456-adf7021"
EXPECTED_CORE="c28c46c3478d7931af611923c92cd8f692a00858"

[[ ${EUID:-$(id -u)} -eq 0 ]] || { ui_fail "Run the HAT probe with sudo/root."; exit 2; }
[[ -e "$DEVICE" ]] || { ui_fail "Modem UART does not exist: $DEVICE"; exit 2; }
[[ -f "$TARGETS" && -f "$PROBE" ]] || { ui_fail "Qualified firmware probe tooling is missing."; exit 2; }

ui_header "YWD-MMDVM-TNC HAT check"
actual_core="$(git -c safe.directory='*' -C "$ROOT/vendor/ywd-1278" rev-parse HEAD 2>>"$YWD_TNC_LOG_FILE" || true)"
[[ "$actual_core" == "$EXPECTED_CORE" ]] || { ui_fail "Qualified modem core mismatch."; exit 2; }
command -v fuser >/dev/null 2>&1 || { ui_fail "fuser is required."; exit 2; }

if fuser "$DEVICE" >/dev/null 2>&1; then
  ui_fail "The modem UART is busy. Stop the active modem service before probing."
  fuser -v "$DEVICE" >>"$YWD_TNC_LOG_FILE" 2>&1 || true
  exit 4
fi
ui_ok "Modem UART is available"

ui_step "Reading HAT firmware identity"
json="$(python3 "$PROBE" --device "$DEVICE" --targets "$TARGETS" --no-application-release --json 2>>"$YWD_TNC_LOG_FILE")"
printf '%s\n' "$json" >>"$YWD_TNC_LOG_FILE"

mapfile -t values < <(
  JSON_PAYLOAD="$json" python3 - "$EXPECTED_TARGET" "$EXPECTED_IDENTITY" <<'PY'
import json,os,sys
obj=json.loads(os.environ["JSON_PAYLOAD"])
expected_target=sys.argv[1]; expected_identity=sys.argv[2]
print(obj.get("identity",""))
print("yes" if obj.get("matched_target_ids")==[expected_target] else "no")
print("yes" if obj.get("identity")==expected_identity else "no")
print("yes" if obj.get("rf_configured") is False else "no")
print("yes" if obj.get("flash_written") is False else "no")
print("yes" if obj.get("option_bytes_written") is False else "no")
PY
)
identity="${values[0]:-}"
[[ "${values[1]:-no}" == yes ]] || { ui_fail "HAT did not uniquely match the qualified target."; exit 2; }
[[ "${values[2]:-no}" == yes ]] || { ui_fail "The exact qualified YWD-MMDVM-TNC firmware is not running."; ui_log "runtime_identity=$identity"; exit 2; }
[[ "${values[3]:-no}" == yes && "${values[4]:-no}" == yes && "${values[5]:-no}" == yes ]] || { ui_fail "Probe safety checks were not clean."; exit 2; }

ui_ok "Qualified HAT firmware verified"
printf 'Target:   %s\n' "$EXPECTED_TARGET"
printf 'Firmware: exact qualified AX25R4 image\n'
printf 'Log:      %s\n' "$YWD_TNC_LOG_FILE"
ui_log "RUNTIME_IDENTITY=$identity MODEM_UART_OPENED=YES GET_VERSION_ONLY=YES RF_CONFIGURED=NO RX_STARTED=NO TX_REQUESTED=NO RF_TRANSMITTED=NO FLASH_WRITTEN=NO OPTION_BYTES_WRITTEN=NO"
