#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

bash "$ROOT/scripts/check-p2-pre-rf.sh"
bash -n "$ROOT/scripts/p3-lan-kiss-physical.sh"
python3 -m py_compile "$ROOT/src/ywdtnc/config.py" "$ROOT/tests/test_config.py"

python3 - "$ROOT" <<'PY'
from pathlib import Path
import socket
import sys
import tempfile

root = Path(sys.argv[1])
from ywdtnc.config import TNCConfigurationError, load_config
from ywd1278.kiss.server import RXOnlyBackend, start_server_thread, stop_server_thread

example = (root / "config/ywd-mmdvm-tnc.example.toml").read_text(encoding="utf-8")
cfg = load_config(root / "config/ywd-mmdvm-tnc.example.toml")
assert cfg.kiss.listen == "127.0.0.1"
assert cfg.kiss.allow_wildcard_bind is False
assert cfg.tx_enabled is False

without = example.replace(
    'listen = "127.0.0.1"\nport = 8001',
    'listen = "0.0.0.0"\nport = 8001',
)
with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as fh:
    fh.write(without)
    path = Path(fh.name)
try:
    try:
        load_config(path)
    except TNCConfigurationError:
        pass
    else:
        raise AssertionError("wildcard bind accepted without explicit opt-in")
finally:
    path.unlink(missing_ok=True)

allowed = example.replace(
    'listen = "127.0.0.1"\nport = 8001\nallow_wildcard_bind = false',
    'listen = "0.0.0.0"\nport = 8001\nallow_wildcard_bind = true',
)
with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as fh:
    fh.write(allowed)
    path = Path(fh.name)
try:
    wildcard = load_config(path)
    assert wildcard.kiss.listen == "0.0.0.0"
    assert wildcard.kiss.allow_wildcard_bind is True
    assert wildcard.tx_enabled is False
finally:
    path.unlink(missing_ok=True)

backend = RXOnlyBackend(history_capacity=0)
server, thread = start_server_thread(backend, host="0.0.0.0", port=0)
try:
    port = int(server.server_address[1])
    with socket.create_connection(("127.0.0.1", port), timeout=1.0):
        pass
finally:
    stop_server_thread(server, thread)

physical = (root / "scripts/p3-lan-kiss-physical.sh").read_text(encoding="utf-8")
assert "P3 LAN RX gate requires persistent radio.tx_enabled=false" in physical
assert "LINBPQ_REMOTE_TCP_ESTABLISHED=PASS" in physical
assert "LINBPQ_LIVE_RX_CONFIRMED=YES" in physical
assert "RF_DIRECTION=RX_ONLY" in physical

print("P3_WILDCARD_BIND_OPT_IN_CONTRACT=PASS")
print("P3_WILDCARD_SOCKET_HOST_TEST=PASS")
print("PERSISTENT_TX_DEFAULT_CHANGED=NO")
print("P3_HOST_CHECK_HARDWARE_ACCESS=NO")
print("P3_HOST_CHECK_RF_TRANSMITTED=NO")
PY

echo "YWD_TNC_P3_LAN_KISS_HOST_CONTRACT=PASS"
echo "HARDWARE_ACCESSED=NO"
echo "RF_TRANSMITTED=NO"
