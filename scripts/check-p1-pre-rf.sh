#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
EXPECTED_CORE="c28c46c3478d7931af611923c92cd8f692a00858"

actual_core="$(git -C "$ROOT/vendor/ywd-1278" rev-parse HEAD)"
[[ "$actual_core" == "$EXPECTED_CORE" ]] || { echo "qualified core mismatch" >&2; exit 2; }

for script in \
  "$ROOT/installer/bootstrap.sh" \
  "$ROOT/installer/install.sh" \
  "$ROOT/firmware/build.sh" \
  "$ROOT/firmware/probe.sh" \
  "$ROOT/firmware/flash.sh" \
  "$ROOT/scripts/preflight.sh" \
  "$ROOT/scripts/p1-rx-physical.sh" \
  "$ROOT/scripts/check-p1-pre-rf.sh"; do
  bash -n "$script"
done

python -m compileall -q "$ROOT/src/ywdtnc" "$ROOT/tests" "$ROOT/firmware/qualified_flash.py"
python -m ywdtnc.firmware --profile "$ROOT/firmware/product-ax25r4.json" show
python -m unittest discover -s "$ROOT/tests" -v
python -m ywdtnc.tncd --config "$ROOT/config/ywd-mmdvm-tnc.example.toml" --framework-self-test

python - "$ROOT/config/ywd-mmdvm-tnc.example.toml" "$ROOT/firmware/product-ax25r4.json" <<'PY'
import json,sys,tomllib
with open(sys.argv[1],"rb") as f: cfg=tomllib.load(f)
profile=json.load(open(sys.argv[2],encoding="utf-8"))
assert cfg["radio"]["tx_enabled"] is False
assert cfg["firmware"]["allow_automatic_flash"] is False
s=profile["safety"]
assert s["automatic_flash_enabled"] is False
assert s["installer_may_flash"] is False
assert s["service_may_auto_flash"] is False
assert s["option_bytes_permitted"] is False
assert s["tx_must_remain_disabled_during_flash"] is True
PY

echo "YWD_TNC_P1_PRE_RF_HOST_CONTRACT=PASS"
echo "HARDWARE_ACCESSED=NO"
echo "MODEM_UART_OPENED=NO"
echo "FLASH_WRITTEN=NO"
echo "RF_TRANSMITTED=NO"
