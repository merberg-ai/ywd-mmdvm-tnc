#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
DEVICE="${YWD_TNC_DEVICE:-/dev/ttyAMA0}"
TARGETS="$ROOT/vendor/ywd-1278/firmware/targets.json"
PROBE="$ROOT/vendor/ywd-1278/firmware/probe_hat.py"
EXPECTED_IDENTITY="MMDVM_HS_Hat-YWD-1278-AX25R4-v0.1.0-alpha1 14.7456MHz ADF7021 FW based on CA6JAU GitID #7ff74ed"
EXPECTED_TARGET="mmdvm-hs-hat-stm32f103-simplex-14.7456-adf7021"
EXPECTED_CORE="c28c46c3478d7931af611923c92cd8f692a00858"

die(){ printf '[FAIL] %s\n' "$*" >&2; exit 2; }

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  die "Run the hardware probe as root: sudo ./firmware/probe.sh"
fi

[[ -e "$DEVICE" ]] || die "Modem UART does not exist: $DEVICE"
[[ -f "$TARGETS" && -f "$PROBE" ]] || die "Pinned firmware probe tooling is missing"
actual_core="$(git -c safe.directory='*' -C "$ROOT/vendor/ywd-1278" rev-parse HEAD 2>/dev/null || true)"
[[ "$actual_core" == "$EXPECTED_CORE" ]] || die "Qualified core mismatch: expected $EXPECTED_CORE got ${actual_core:-UNKNOWN}"
command -v fuser >/dev/null 2>&1 || die "fuser is required"

if fuser "$DEVICE" >/dev/null 2>&1; then
  echo "[FAIL] Modem UART is busy. Stop the active modem service before probing: $DEVICE" >&2
  fuser -v "$DEVICE" >&2 || true
  exit 4
fi

echo "===== YWD-MMDVM-TNC SAFE HAT PROBE ====="
json="$(python3 "$PROBE" \
  --device "$DEVICE" \
  --targets "$TARGETS" \
  --no-application-release \
  --json)"

mapfile -t values < <(
  JSON_PAYLOAD="$json" python3 - "$EXPECTED_TARGET" "$EXPECTED_IDENTITY" <<'PY'
import json,os,sys
obj=json.loads(os.environ["JSON_PAYLOAD"])
expected_target=sys.argv[1]
expected_identity=sys.argv[2]
print(obj.get("identity",""))
print("yes" if obj.get("matched_target_ids")==[expected_target] else "no")
print("yes" if obj.get("identity")==expected_identity else "no")
print("yes" if obj.get("rf_configured") is False else "no")
print("yes" if obj.get("flash_written") is False else "no")
print("yes" if obj.get("option_bytes_written") is False else "no")
PY
)

identity="${values[0]:-}"
[[ "${values[1]:-no}" == yes ]] || die "HAT target did not uniquely match the qualified product target"
[[ "${values[2]:-no}" == yes ]] || die "HAT is not running the exact qualified AX25R4 firmware: $identity"
[[ "${values[3]:-no}" == yes && "${values[4]:-no}" == yes && "${values[5]:-no}" == yes ]] \
  || die "Probe safety markers were not clean"

echo "YWD_TNC_HAT_PROBE=PASS"
echo "TARGET_ID=$EXPECTED_TARGET"
echo "RUNTIME_IDENTITY=$identity"
echo "MODEM_UART_OPENED=YES"
echo "GET_VERSION_ONLY=YES"
echo "RF_CONFIGURED=NO"
echo "RX_STARTED=NO"
echo "TX_REQUESTED=NO"
echo "RF_TRANSMITTED=NO"
echo "FLASH_WRITTEN=NO"
echo "OPTION_BYTES_WRITTEN=NO"
