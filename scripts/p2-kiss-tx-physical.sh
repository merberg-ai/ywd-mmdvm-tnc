#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="${YWD_TNC_CONFIG:-/etc/ywd-mmdvm-tnc/config.toml}"
SERVICE="ywd-mmdvm-tnc.service"
VENV=/opt/ywd-mmdvm-tnc/venv
GATE="$VENV/bin/ywd-tnc-p2-gate"
HAT_CONTROL="$ROOT/vendor/ywd-1278/firmware/hat_control.py"
DEVICE="${YWD_TNC_DEVICE:-/dev/ttyAMA0}"
AUTH=P2-TX-ONCE-145050

fail(){ printf '[FAIL] %s\n' "$*" >&2; exit 2; }

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  fail "Run P2 as root: sudo ./scripts/p2-kiss-tx-physical.sh"
fi
[[ -f "$CONFIG" ]] || fail "Installed configuration is missing: $CONFIG"
[[ -x "$GATE" ]] || fail "Installed P2 gate is missing; run sudo ./installer/install.sh from this checkpoint"
[[ -f "$HAT_CONTROL" ]] || fail "Pinned HAT control helper is missing"
command -v fuser >/dev/null 2>&1 || fail "fuser is required"

"$VENV/bin/python" - "$CONFIG" <<'PY'
import sys
from ywdtnc.config import load_config
from ywdtnc import QUALIFIED_TX_FREQUENCY_HZ, QUALIFIED_TX_POWER, QUALIFIED_FIRMWARE_IDENTITY
c=load_config(sys.argv[1])
assert c.tx_enabled is False, "persistent config must have tx_enabled=false"
assert c.frequency_hz == QUALIFIED_TX_FREQUENCY_HZ == 145_050_000
assert c.tx_power == QUALIFIED_TX_POWER == 200
assert c.required_identity == QUALIFIED_FIRMWARE_IDENTITY
assert c.kiss.enabled and c.kiss.listen == "127.0.0.1" and c.kiss.port == 8001
print("P2_PERSISTENT_CONFIG=PASS")
print("PERSISTENT_TX_ENABLED=NO")
print("P2_TEMPORARY_TX_PROFILE=145.050MHz/POWER200")
PY

was_active=0
if systemctl is-active --quiet "$SERVICE"; then
  was_active=1
fi

cleanup(){
  rc=$?
  trap - EXIT
  if [[ $was_active -eq 1 ]]; then
    systemctl start "$SERVICE" >/dev/null 2>&1 || true
  fi
  exit "$rc"
}
trap cleanup EXIT

systemctl stop "$SERVICE" >/dev/null 2>&1 || true
for _ in $(seq 1 30); do
  if ! fuser "$DEVICE" >/dev/null 2>&1; then break; fi
  sleep 0.1
done
if fuser "$DEVICE" >/dev/null 2>&1; then
  fuser -v "$DEVICE" >&2 || true
  fail "modem UART is still owned after stopping the normal service"
fi

echo "===== YWD-MMDVM-TNC P2 PHYSICAL TX AUTHORIZATION ====="
echo "PERSISTENT_TX_ENABLED=NO"
echo "QUALIFICATION_RF_FREQUENCY_MHZ=145.050"
echo "QUALIFICATION_TX_POWER=200"
echo "TX_REQUEST_LIMIT=ONE"
echo "CLIENT_AUTOMATIC_RETRY=NO"
echo "The gate will transmit exactly one qualification AX.25 UI frame."
printf 'Type %s to arm this one-shot physical TX gate: ' "$AUTH"
read -r typed
[[ "$typed" == "$AUTH" ]] || fail "P2 one-shot TX authorization not confirmed"

echo "P2_ONE_SHOT_TX_AUTHORIZED=YES"
python3 "$HAT_CONTROL" application-release --config "$CONFIG"

set +e
"$GATE" --config "$CONFIG" --authorize "$AUTH"
rc=$?
set -e

if [[ $rc -ne 0 ]]; then
  echo "YWD_TNC_P2_PHYSICAL=FAIL:gate_rc=$rc" >&2
  exit "$rc"
fi

echo "PERSISTENT_CONFIG_MODIFIED=NO"
echo "PERSISTENT_TX_ENABLED=NO"
echo "YWD_TNC_P2_WRAPPER=PASS"
