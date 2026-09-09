#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="$ROOT/src:$ROOT/vendor/ywd-1278/src${PYTHONPATH:+:$PYTHONPATH}"

python3 -m py_compile \
  "$ROOT/src/ywdtnc/config.py" \
  "$ROOT/src/ywdtnc/rf_profiles.py" \
  "$ROOT/src/ywdtnc/profile_cli.py" \
  "$ROOT/tests/test_config.py" \
  "$ROOT/tests/test_rf_profiles.py"

python3 "$ROOT/tests/test_config.py"
python3 "$ROOT/tests/test_rf_profiles.py"

python3 - "$ROOT" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
init = (root / "src/ywdtnc/__init__.py").read_text(encoding="utf-8")
engine = (root / "src/ywdtnc/engine.py").read_text(encoding="utf-8")
profiles = (root / "src/ywdtnc/rf_profiles.py").read_text(encoding="utf-8")
cli = (root / "src/ywdtnc/profile_cli.py").read_text(encoding="utf-8")
pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
installer = (root / "installer/install.sh").read_text(encoding="utf-8")

assert "QUALIFIED_TX_FREQUENCY_HZ = 145_050_000" in init
assert "APRS_TX_FREQUENCY_HZ = 144_390_000" in init
assert "APRS_TX_POWER = 200" in init
assert "PERMITTED_TX_PROFILES" in init
assert "ProductTXModemOwner" in engine
assert "owner.apply_tx_profile(" in engine
assert "apply_tx_qualification_profile" not in engine
assert "apply_product_tx_profile" in profiles
assert "unsupported TX profile" in profiles
assert '"packet"' in cli and '"aprs"' in cli and '"rx-only"' in cli
assert "shutil.copy2(config_path, backup_path)" in cli
assert "systemctl\", \"restart" in cli
assert 'ywd-tnc-profile = "ywdtnc.profile_cli:main"' in pyproject
assert 'ln -sfn "$VENV/bin/ywd-tnc-profile" /usr/local/bin/ywd-tnc-profile' in installer

print("RF_PROFILE_PACKET_145050_200=PASS")
print("RF_PROFILE_APRS_144390_200=PASS")
print("RF_PROFILE_ARBITRARY_TX_REJECTED=PASS")
print("RF_PROFILE_SWITCHER_BACKUP_ROLLBACK_CONTRACT=PASS")
print("RF_PROFILE_HOST_CHECK_HARDWARE_ACCESS=NO")
print("RF_PROFILE_HOST_CHECK_RF_TRANSMITTED=NO")
PY

echo "YWD_TNC_RF_PROFILE_HOST_CONTRACT=PASS"
echo "HARDWARE_ACCESSED=NO"
echo "RF_TRANSMITTED=NO"
