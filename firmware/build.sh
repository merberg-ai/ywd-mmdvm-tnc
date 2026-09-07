#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE="$ROOT/firmware/product-ax25r4.json"
CORE="$ROOT/vendor/ywd-1278"
EXPECTED_CORE="c28c46c3478d7931af611923c92cd8f692a00858"
JOBS="${YWD_TNC_FIRMWARE_BUILD_JOBS:-}"

die(){ printf '[FAIL] %s\n' "$*" >&2; exit 2; }

if [[ ${EUID:-$(id -u)} -eq 0 ]]; then
  die "Firmware builds are intentionally non-root. Run this script without sudo."
fi

[[ -f "$PROFILE" ]] || die "Missing product firmware profile: $PROFILE"
[[ -f "$CORE/firmware/build-packet-rssi-ywd1278.py" ]] || die "Pinned YWD-1278 core is not initialized"

actual_core="$(git -c safe.directory='*' -C "$CORE" rev-parse HEAD 2>/dev/null || true)"
[[ "$actual_core" == "$EXPECTED_CORE" ]] || die "Qualified core mismatch: expected $EXPECTED_CORE got ${actual_core:-UNKNOWN}"

if [[ -z "$JOBS" ]]; then
  if command -v nproc >/dev/null 2>&1; then JOBS="$(nproc)"; else JOBS=1; fi
fi
[[ "$JOBS" =~ ^[0-9]+$ ]] && (( JOBS >= 1 )) || die "YWD_TNC_FIRMWARE_BUILD_JOBS must be a positive integer"

for tool in git make arm-none-eabi-gcc arm-none-eabi-g++ arm-none-eabi-objcopy python3; do
  command -v "$tool" >/dev/null 2>&1 || die "Missing firmware build dependency: $tool"
done

artifact_rel="$(python3 - "$PROFILE" <<'PY'
import json,sys
print(json.load(open(sys.argv[1], encoding='utf-8'))['artifact_relative_path'])
PY
)"
artifact="$ROOT/$artifact_rel"

printf '%s\n' "===== YWD-MMDVM-TNC QUALIFIED AX25R4 BUILD ====="
printf 'QUALIFIED_CORE_COMMIT=%s\n' "$EXPECTED_CORE"
printf 'BUILD_JOBS=%s\n' "$JOBS"
printf 'HARDWARE_ACCESS=NO\nGPIO_ACCESSED=NO\nFLASH_WRITTEN=NO\nRF_TRANSMITTED=NO\n'

# The frozen builder remains inside the pinned YWD-1278 provenance submodule.
# It reconstructs the exact qualified image twice and proves reproducibility.
python3 "$CORE/firmware/build-packet-rssi-ywd1278.py" --jobs "$JOBS"

PYTHONPATH="$ROOT/src" python3 -m ywdtnc.firmware \
  --profile "$PROFILE" verify-artifact --firmware "$artifact"

echo "YWD_TNC_QUALIFIED_FIRMWARE_BUILD=PASS"
echo "PRODUCT_FIRMWARE=$artifact"
echo "HARDWARE_ACCESS=NO"
echo "GPIO_ACCESSED=NO"
echo "FLASH_WRITTEN=NO"
echo "RF_TRANSMITTED=NO"
