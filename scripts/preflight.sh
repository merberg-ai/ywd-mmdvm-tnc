#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
EXPECTED_CORE="c28c46c3478d7931af611923c92cd8f692a00858"
CONFIG="${1:-$ROOT/config/ywd-mmdvm-tnc.example.toml}"
DEVICE="${YWD_TNC_DEVICE:-/dev/ttyAMA0}"
FULL_TOOLCHAIN="${YWD_TNC_PREFLIGHT_FULL_TOOLCHAIN:-0}"

die(){ printf '[FAIL] %s\n' "$*" >&2; exit 2; }
pass(){ printf '[PASS] %s\n' "$*"; }

printf '%s\n' "===== YWD-MMDVM-TNC MACHINE PREFLIGHT ====="

[[ "$(uname -s)" == Linux ]] || die "Linux is required"
pass "Linux host"

[[ -f "$ROOT/vendor/ywd-1278/src/ywd1278/__init__.py" ]] || die "Pinned submodule is missing; run git submodule update --init --recursive"
actual_core="$(git -c safe.directory='*' -C "$ROOT/vendor/ywd-1278" rev-parse HEAD 2>/dev/null || true)"
[[ "$actual_core" == "$EXPECTED_CORE" ]] || die "Qualified core mismatch: expected $EXPECTED_CORE got ${actual_core:-UNKNOWN}"
pass "Qualified YWD-1278 core pin"

for tool in python3 git fuser; do
  command -v "$tool" >/dev/null 2>&1 || die "Missing runtime dependency: $tool"
done
pass "Runtime tools"

python_version="$(python3 - <<'PY'
import sys
print(f"{sys.version_info.major}.{sys.version_info.minor}")
raise SystemExit(0 if sys.version_info >= (3,11) else 1)
PY
)" || die "Python 3.11 or newer is required"
printf 'PYTHON_VERSION=%s\n' "$python_version"

if [[ -r /proc/device-tree/model ]]; then
  model="$(tr -d '\0' </proc/device-tree/model)"
  printf 'HOST_MODEL=%s\n' "$model"
  [[ "$model" == *"Raspberry Pi 5"* ]] || die "Qualified GPIO bootloader control currently requires Raspberry Pi 5"
  pass "Qualified Raspberry Pi 5 host"
else
  echo "[WARN] /proc/device-tree/model is unavailable; CI/container mode only"
fi

[[ -e "$DEVICE" ]] || die "Modem UART does not exist: $DEVICE"
printf 'MODEM_DEVICE=%s\n' "$DEVICE"

if fuser "$DEVICE" >/dev/null 2>&1; then
  echo "[FAIL] Modem UART is currently busy: $DEVICE" >&2
  fuser -v "$DEVICE" >&2 || true
  exit 4
fi
pass "Modem UART is idle"

[[ -f "$CONFIG" ]] || die "Configuration not found: $CONFIG"
PYTHONPATH="$ROOT/src:$ROOT/vendor/ywd-1278/src" \
  python3 -m ywdtnc.tncd --config "$CONFIG" --framework-self-test
pass "TX-disabled product configuration"

if [[ "$FULL_TOOLCHAIN" == 1 ]]; then
  for tool in make arm-none-eabi-gcc arm-none-eabi-g++ arm-none-eabi-objcopy stm32flash pinctrl; do
    command -v "$tool" >/dev/null 2>&1 || die "Missing full firmware/flash dependency: $tool"
  done
  pass "Firmware build/flash toolchain"
fi

echo "YWD_TNC_MACHINE_PREFLIGHT=PASS"
echo "MODEM_UART_OPENED=NO"
echo "RF_CONFIGURED=NO"
echo "RF_TRANSMITTED=NO"
echo "FLASH_WRITTEN=NO"
