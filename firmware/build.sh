#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/installer/lib/ui.sh"
YWD_TNC_LOG_BASENAME=firmware-build
ui_init "$@"

PROFILE="$ROOT/firmware/product-ax25r4.json"
CORE="$ROOT/vendor/ywd-1278"
EXPECTED_CORE="c28c46c3478d7931af611923c92cd8f692a00858"
JOBS="${YWD_TNC_FIRMWARE_BUILD_JOBS:-}"

[[ ${EUID:-$(id -u)} -ne 0 ]] || { ui_fail "Build firmware without sudo/root."; exit 2; }
[[ -f "$PROFILE" ]] || { ui_fail "Product firmware profile is missing."; exit 2; }
[[ -f "$CORE/firmware/build-packet-rssi-ywd1278.py" ]] || { ui_fail "Qualified modem core is not initialized."; exit 2; }

ui_header "YWD-MMDVM-TNC firmware build"
actual_core="$(git -c safe.directory='*' -C "$CORE" rev-parse HEAD 2>>"$YWD_TNC_LOG_FILE" || true)"
[[ "$actual_core" == "$EXPECTED_CORE" ]] || { ui_fail "Qualified modem core mismatch."; exit 2; }
ui_ok "Qualified source revision verified"

if [[ -z "$JOBS" ]]; then
  if command -v nproc >/dev/null 2>&1; then JOBS="$(nproc)"; else JOBS=1; fi
fi
[[ "$JOBS" =~ ^[0-9]+$ ]] && (( JOBS >= 1 )) || { ui_fail "YWD_TNC_FIRMWARE_BUILD_JOBS must be a positive integer."; exit 2; }

for tool in git make arm-none-eabi-gcc arm-none-eabi-g++ arm-none-eabi-objcopy python3; do
  command -v "$tool" >/dev/null 2>&1 || { ui_fail "Missing build dependency: $tool"; exit 2; }
done

artifact_rel="$(python3 - "$PROFILE" <<'PY'
import json,sys
print(json.load(open(sys.argv[1], encoding='utf-8'))['artifact_relative_path'])
PY
)"
artifact="$ROOT/$artifact_rel"

ui_run "Building the qualified AX.25 firmware twice (reproducibility check)" python3 "$CORE/firmware/build-packet-rssi-ywd1278.py" --jobs "$JOBS"
ui_run "Verifying firmware size and SHA-256" env PYTHONPATH="$ROOT/src" python3 -m ywdtnc.firmware --profile "$PROFILE" verify-artifact --firmware "$artifact"

ui_header "Build complete"
printf 'Firmware: %s\n' "$artifact"
printf 'Log:      %s\n' "$YWD_TNC_LOG_FILE"
ui_log "HARDWARE_ACCESS=NO GPIO_ACCESSED=NO FLASH_WRITTEN=NO RF_TRANSMITTED=NO"
