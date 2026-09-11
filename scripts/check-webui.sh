#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
BASE="a941f39f7393588029fb6e0ec274bd653dd07da9"

fail() { echo "WEBUI_HOST_GATE=FAIL: $*" >&2; exit 1; }

git cat-file -e "$BASE^{commit}" 2>/dev/null || fail "qualified dev-webui base commit is unavailable"
python3 -m py_compile src/ywdweb/*.py
python3 -m unittest discover -s tests -p 'test_webui*.py' -v
bash -n installer/install.sh
bash -n scripts/webui-physical.sh
python3 -m ywdweb.app --config config/ywd-webui.example.toml --framework-self-test

# The WebUI is not allowed to modify the physically-qualified runtime/monitor closure.
git diff --quiet "$BASE" -- \
  src/ywd1278 \
  src/ywdtnc/engine.py \
  src/ywdtnc/monitor.py \
  src/ywdtnc/monitor_tx.py \
  src/ywdtnc/packetlog.py \
  || fail "qualified modem/monitor path changed"

# Source boundary: no imports/calls into packet TX/modem internals.
if grep -R -nE '(^|[[:space:]])(from|import)[[:space:]]+(ywd1278|ywdtnc\.engine|ywdtnc\.monitor|ywdtnc\.monitor_tx|ywdtnc\.packetlog|ywdtnc\.agw)' src/ywdweb; then
  fail "WebUI imports qualified modem/TNC implementation"
fi
if grep -R -nE 'KISSMessage|transmit_selector_burst|ProductTXModemOwner|/dev/serial|/dev/tty|\.send\(|\.sendall\(|\.sendto\(' src/ywdweb; then
  fail "WebUI contains a transmit/device/socket-write API"
fi
if grep -R -nE 'def do_(POST|PUT|PATCH|DELETE)' src/ywdweb; then
  fail "WebUI exposes an HTTP mutation handler"
fi

grep -q 'socket.create_connection' src/ywdweb/monitor_client.py || fail "monitor reader connection missing"
grep -q 'makefile("r"' src/ywdweb/monitor_client.py || fail "monitor reader is not receive-only stream code"
grep -q 'DynamicUser=true' systemd/ywd-webui.service || fail "WebUI service must use DynamicUser"
grep -q 'ProtectSystem=strict' systemd/ywd-webui.service || fail "WebUI service filesystem hardening missing"
grep -q 'PrivateDevices=true' systemd/ywd-webui.service || fail "WebUI service device isolation missing"
grep -q 'CapabilityBoundingSet=' systemd/ywd-webui.service || fail "WebUI capability drop missing"
grep -q '127.0.0.1' config/ywd-webui.example.toml || fail "safe loopback default missing"

if grep -REn 'https?://' src/ywdweb/static; then
  fail "WebUI static assets must not depend on remote resources"
fi

if command -v node >/dev/null 2>&1; then
  node --check src/ywdweb/static/app.js
fi

echo WEBUI_HOST_GATE=PASS
echo QUALIFIED_MODEM_MONITOR_PATH_CHANGED=NO
echo HTTP_MUTATION_API_PRESENT=NO
echo MONITOR_SOCKET_WRITE_API_PRESENT=NO
echo KISS_OR_MODEM_IMPORT_PRESENT=NO
echo MODEM_UART_OPENED=NO
echo RF_TRANSMITTED=NO
