#!/usr/bin/env bash
set -Eeuo pipefail

CONFIG="${1:-/etc/ywd-mmdvm-tnc/config.toml}"
SERVICE="ywd-mmdvm-tnc.service"
PYTHON="/opt/ywd-mmdvm-tnc/venv/bin/python"

if (( EUID != 0 )); then
  echo "YWD_TNC_P3_LAN_RX=FAIL:must_run_as_root" >&2
  exit 2
fi
if [[ ! -x "$PYTHON" ]]; then
  echo "YWD_TNC_P3_LAN_RX=FAIL:installed_python_missing:$PYTHON" >&2
  exit 3
fi
if ! command -v ss >/dev/null 2>&1; then
  echo "YWD_TNC_P3_LAN_RX=FAIL:ss_missing" >&2
  exit 4
fi

echo "===== YWD-MMDVM-TNC P3 LINBPQ LAN RX QUALIFICATION ====="
"$PYTHON" - "$CONFIG" <<'PY'
from pathlib import Path
import sys
from ywdtnc.config import load_config

path = Path(sys.argv[1])
cfg = load_config(path)
if cfg.frequency_hz != 145_050_000:
    raise SystemExit("P3 requires radio.frequency_mhz=145.050")
if cfg.tx_enabled:
    raise SystemExit("P3 LAN RX gate requires persistent radio.tx_enabled=false")
if not cfg.kiss.enabled:
    raise SystemExit("P3 requires KISS enabled")
if cfg.kiss.listen != "0.0.0.0":
    raise SystemExit("P3 requires kiss.listen=0.0.0.0")
if not cfg.kiss.allow_wildcard_bind:
    raise SystemExit("P3 requires kiss.allow_wildcard_bind=true")
if cfg.kiss.port != 8001:
    raise SystemExit("P3 requires kiss.port=8001")
print("P3_CONFIG=PASS")
print("RF_FREQUENCY_MHZ=145.050")
print("PERSISTENT_TX_ENABLED=NO")
print("KISS_LISTEN=0.0.0.0")
print("KISS_PORT=8001")
print("KISS_WILDCARD_BIND_EXPLICITLY_AUTHORIZED=YES")
PY

systemctl restart "$SERVICE"

ready=0
for _ in $(seq 1 100); do
  if systemctl is-active --quiet "$SERVICE" && \
     ss -H -ltn '( sport = :8001 )' 2>/dev/null | grep -Eq '(^|[[:space:]])(0\.0\.0\.0|\*):8001([[:space:]]|$)'; then
    ready=1
    break
  fi
  sleep 0.1
done
if (( ! ready )); then
  echo "YWD_TNC_P3_LAN_RX=FAIL:kiss_wildcard_listener_not_ready" >&2
  journalctl -u "$SERVICE" -n 30 --no-pager >&2 || true
  exit 10
fi

echo "YWD_TNCD_SERVICE=ACTIVE"
echo "KISS_WILDCARD_LISTENER=PASS"
echo "LISTENER_LINE=$(ss -H -ltn '( sport = :8001 )' | head -n 1)"
echo "HOST_LAN_IPV4=$(hostname -I 2>/dev/null | xargs || true)"
journalctl -u "$SERVICE" -n 15 --no-pager || true

cat <<'EOF'
===== REMOTE LINBPQ CONNECTION GATE =====
Start/restart LinBPQ on the OTHER LAN system with its KISS port pointed at this
Pi's LAN IPv4 address, TCP port 8001. Do not use 0.0.0.0 as LinBPQ's IPADDR;
0.0.0.0 is only the listener bind address on this Pi.
When LinBPQ reports the KISS TCP port connected, type LINBPQ-KISS-CONNECTED:
EOF
read -r connected_token
if [[ "$connected_token" != "LINBPQ-KISS-CONNECTED" ]]; then
  echo "YWD_TNC_P3_LAN_RX=FAIL:operator_connection_confirmation" >&2
  exit 20
fi

remote=0
remote_rows=""
for _ in $(seq 1 100); do
  remote_rows="$(ss -H -tn state established '( sport = :8001 )' 2>/dev/null | grep -v '127\.0\.0\.1' || true)"
  if [[ -n "$remote_rows" ]]; then
    remote=1
    break
  fi
  sleep 0.1
done
if (( ! remote )); then
  echo "YWD_TNC_P3_LAN_RX=FAIL:no_nonloopback_established_kiss_client" >&2
  ss -H -tn state established '( sport = :8001 )' >&2 || true
  exit 21
fi

echo "LINBPQ_REMOTE_TCP_ESTABLISHED=PASS"
echo "ESTABLISHED_KISS_CONNECTIONS_BEGIN"
printf '%s\n' "$remote_rows"
echo "ESTABLISHED_KISS_CONNECTIONS_END"

cat <<'EOF'
===== LINBPQ LIVE RF RX GATE =====
Transmit ONE normal 1200-baud AX.25 packet on 145.050 MHz from another station.
Confirm that exact live frame appears in LinBPQ's monitor on the remote host.
After it appears ONCE, type LINBPQ-LIVE-RX-MATCH-ONE:
EOF
read -r rx_token
if [[ "$rx_token" != "LINBPQ-LIVE-RX-MATCH-ONE" ]]; then
  echo "YWD_TNC_P3_LAN_RX=FAIL:operator_rx_confirmation" >&2
  exit 30
fi

if ! systemctl is-active --quiet "$SERVICE"; then
  echo "YWD_TNC_P3_LAN_RX=FAIL:service_not_active_after_gate" >&2
  exit 31
fi
if ! ss -H -tn state established '( sport = :8001 )' 2>/dev/null | grep -v '127\.0\.0\.1' | grep -q ':8001'; then
  echo "YWD_TNC_P3_LAN_RX=FAIL:linbpq_connection_not_established_after_gate" >&2
  exit 32
fi

echo "LINBPQ_LIVE_RX_CONFIRMED=YES"
echo "PERSISTENT_TX_ENABLED=NO"
echo "RF_DIRECTION=RX_ONLY"
echo "LINBPQ_KISS_TCP_LAN=PASS"
echo "YWD_TNC_P3_LAN_RX=PASS"
