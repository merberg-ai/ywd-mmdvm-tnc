#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

bash "$ROOT/scripts/check-p1-pre-rf.sh"
bash -n "$ROOT/scripts/p2-kiss-tx-physical.sh"
python3 -m py_compile "$ROOT/src/ywdtnc/p2_qualification.py" "$ROOT/tests/test_p2_qualification.py"

python3 - "$ROOT" <<'PY'
from pathlib import Path
import sys
root=Path(sys.argv[1])
source=(root/'src/ywdtnc/p2_qualification.py').read_text(encoding='utf-8')
wrapper=(root/'scripts/p2-kiss-tx-physical.sh').read_text(encoding='utf-8')
assert source.count('sock.sendall(') == 1
assert 'CLIENT_RETRY_COUNT=0' in source
assert 'NO_SECOND_INTERNAL_DISPATCH_AFTER_HOLD=PASS' in source
assert 'SAME_KISS_CONNECTION_RX=PASS' in source
assert 'P2-TX-ONCE-145050' in wrapper
assert 'persistent config must have tx_enabled=false' in wrapper
assert 'tx_enabled = true' not in wrapper
print('P2_ONE_SHOT_KISS_CONTRACT=PASS')
print('PERSISTENT_TX_DEFAULT_CHANGED=NO')
print('P2_HOST_CHECK_HARDWARE_ACCESS=NO')
print('P2_HOST_CHECK_RF_TRANSMITTED=NO')
PY

echo "YWD_TNC_P2_PRE_RF_HOST_CONTRACT=PASS"
echo "HARDWARE_ACCESSED=NO"
echo "RF_TRANSMITTED=NO"
