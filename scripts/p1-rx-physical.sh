#!/usr/bin/env bash
set -Eeuo pipefail

CONFIG="${YWD_TNC_CONFIG:-/etc/ywd-mmdvm-tnc/config.toml}"
SERVICE="ywd-mmdvm-tnc.service"
GATE="/opt/ywd-mmdvm-tnc/venv/bin/ywd-tnc-rx-gate"
TIMEOUT="${YWD_TNC_P1_RX_TIMEOUT:-120}"
CONNECT_TIMEOUT="${YWD_TNC_P1_CONNECT_TIMEOUT:-10}"

fail(){ printf '[FAIL] %s\n' "$*" >&2; exit 2; }

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  fail "Run the physical RX gate as root: sudo ./scripts/p1-rx-physical.sh"
fi
[[ -f "$CONFIG" ]] || fail "Installed configuration is missing: $CONFIG"
[[ -x "$GATE" ]] || fail "Installed RX gate is missing; run installer/bootstrap.sh or installer/install.sh"

mapfile -t cfg < <(python3 - "$CONFIG" <<'PY'
import sys,tomllib
with open(sys.argv[1],"rb") as f: d=tomllib.load(f)
r=d.get("radio",{}); k=d.get("kiss",{}); fw=d.get("firmware",{})
print(str(r.get("frequency_mhz","")))
print("yes" if r.get("tx_enabled") is False else "no")
print("yes" if k.get("enabled") is True else "no")
print(str(k.get("listen","")))
print(str(k.get("port","")))
print(str(fw.get("required_identity","")))
PY
)

[[ "${cfg[0]:-}" == "145.05" || "${cfg[0]:-}" == "145.050" ]] || fail "P1 gate requires radio.frequency_mhz=145.050"
[[ "${cfg[1]:-no}" == yes ]] || fail "P1 gate requires radio.tx_enabled=false"
[[ "${cfg[2]:-no}" == yes ]] || fail "P1 gate requires KISS enabled"
[[ "${cfg[3]:-}" == "127.0.0.1" ]] || fail "P1 gate requires loopback KISS listener 127.0.0.1"
[[ "${cfg[4]:-}" == "8001" ]] || fail "P1 gate requires KISS port 8001"
[[ "${cfg[5]:-}" == "MMDVM_HS_Hat-YWD-1278-AX25R4-v0.1.0-alpha1 14.7456MHz ADF7021 FW based on CA6JAU GitID #7ff74ed" ]] \
  || fail "P1 gate requires the exact qualified AX25R4 firmware identity"

was_active=0
if systemctl is-active --quiet "$SERVICE"; then
  was_active=1
fi
cleanup(){
  if [[ $was_active -eq 0 ]]; then
    systemctl stop "$SERVICE" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

echo "===== YWD-MMDVM-TNC P1 PHYSICAL RX QUALIFICATION ====="
echo "RF_FREQUENCY_MHZ=145.050"
echo "TX_ENABLED=NO"
echo "KISS_ENDPOINT=127.0.0.1:8001"
echo "KISS_GATE_WRITES=NO"
echo "KISS_READY_WAIT_SECONDS=$CONNECT_TIMEOUT"
echo "RF_WAIT_LIMIT_SECONDS=$TIMEOUT"

if [[ $was_active -eq 0 ]]; then
  systemctl start "$SERVICE"
fi

for _ in $(seq 1 30); do
  systemctl is-active --quiet "$SERVICE" && break
  sleep 0.2
done
systemctl is-active --quiet "$SERVICE" || {
  journalctl -u "$SERVICE" -n 80 --no-pager >&2 || true
  fail "ywd-mmdvm-tnc service did not become active"
}

echo "YWD_TNCD_SERVICE=ACTIVE"
journalctl -u "$SERVICE" -n 12 --no-pager || true
printf '\nWaiting for ywd-tncd to finish modem startup and open TCP KISS...\n\n'

set +e
"$GATE" --host 127.0.0.1 --port 8001 --connect-timeout "$CONNECT_TIMEOUT" --timeout "$TIMEOUT"
rc=$?
set -e

if [[ $rc -ne 0 ]]; then
  if ! systemctl is-active --quiet "$SERVICE"; then
    echo "YWD_TNCD_SERVICE=FAILED_BEFORE_OR_DURING_GATE" >&2
  else
    echo "YWD_TNCD_SERVICE=ACTIVE_AT_GATE_FAILURE" >&2
  fi
  journalctl -u "$SERVICE" -n 80 --no-pager >&2 || true
  echo "YWD_TNC_P1_PHYSICAL_RX=FAIL:gate_rc=$rc" >&2
  exit "$rc"
fi

echo "YWD_TNC_P1_PHYSICAL_RX=PASS"
echo "TX_ENABLED=NO"
echo "KISS_GATE_WRITES=NO"
echo "RF_DIRECTION=RX_ONLY"
