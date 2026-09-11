#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

python3 -m compileall -q src/ywdtnc
python3 -m unittest discover -s tests -p 'test_monitor*.py' -v
python3 -m unittest discover -s tests -p 'test_packetlog.py' -v

python3 - <<'PY'
from pathlib import Path
import re
import tempfile

from ywdtnc.config import load_config

root = Path('.')
example = root / 'config' / 'ywd-mmdvm-tnc.example.toml'
config = load_config(example)
assert config.monitor.enabled is True
assert config.monitor.listen == '127.0.0.1'
assert config.monitor.port == 8002
assert config.monitor.allow_wildcard_bind is False

text = example.read_text(encoding='utf-8')
legacy = re.sub(
    r'\n# Passive newline-delimited JSON event stream\.[\s\S]*?\n\[firmware\]\n',
    '\n[firmware]\n',
    text,
    count=1,
)
assert legacy != text
with tempfile.NamedTemporaryFile('w', suffix='.toml', delete=False) as handle:
    handle.write(legacy)
    legacy_path = Path(handle.name)
try:
    old = load_config(legacy_path)
finally:
    legacy_path.unlink(missing_ok=True)
assert old.monitor.enabled is False
assert old.monitor.listen == '127.0.0.1'
assert old.monitor.port == 8002

packetlog = (root / 'src' / 'ywdtnc' / 'packetlog.py').read_text(encoding='utf-8')
for forbidden in ('.send(', '.sendall(', '.sendto(', 'transmit_selector_burst', 'KISSMessage('):
    assert forbidden not in packetlog, forbidden
assert 'socket.create_connection' in packetlog

monitor = (root / 'src' / 'ywdtnc' / 'monitor.py').read_text(encoding='utf-8')
assert 'queue.put_nowait(payload)' in monitor
assert 'no history' in monitor.lower()

engine = (root / 'src' / 'ywdtnc' / 'engine.py').read_text(encoding='utf-8')
for event in ('rx.frame', 'tx.submitted', 'tx.rejected'):
    assert event in engine

tx = (root / 'src' / 'ywdtnc' / 'monitor_tx.py').read_text(encoding='utf-8')
for event in ('tx.queued', 'tx.channel_clear', 'tx.dispatched', 'tx.complete', 'tx.timeout', 'tx.failed'):
    assert event in tx

service = (root / 'systemd' / 'ywd-packetlog.service').read_text(encoding='utf-8')
assert 'ExecStart=/opt/ywd-mmdvm-tnc/venv/bin/ywd-packetlog' in service
assert 'Requires=ywd-mmdvm-tnc.service' in service
assert 'LogsDirectory=ywd-packetlog' in service

low_install = (root / 'installer' / 'install.sh').read_text(encoding='utf-8')
assert 'ywd-packetlog.service' in low_install
assert 'ywd-packetlog' in low_install
for line in low_install.splitlines():
    if 'systemctl' in line and 'enable' in line:
        assert 'packetlog' not in line.lower(), line

guided = (root / 'installer' / 'setup.sh').read_text(encoding='utf-8')
assert '[monitor]' in guided
assert '127.0.0.1:8002' in guided
assert 'ui_prompt_yes_no packetlog_answer "Enable the passive packet activity logger at boot?" no' in guided
assert 'PACKETLOG_WAS_ENABLED' in guided
assert 'Migrating/restarting ywd-packetlog on the product monitor stream' in guided

print('MONITOR_CONFIG_BACKCOMPAT=PASS')
print('MONITOR_DEFAULT_BIND=127.0.0.1:8002')
print('MONITOR_HISTORY_REPLAY=NONE')
print('MONITOR_SUBSCRIBER_BACKPRESSURE=DROP_ONLY')
print('PACKETLOGGER_TX_API=NONE')
print('PACKETLOGGER_PERSISTENCE=HUMAN_PLUS_JSONL')
print('PACKETLOGGER_AUTO_ENABLE=EXPLICIT_ONLY')
print('MONITOR_EVENTS_CONTRACT=PASS')
print('MODEM_UART_OPENED=NO')
print('RF_TRANSMITTED=NO')
PY
