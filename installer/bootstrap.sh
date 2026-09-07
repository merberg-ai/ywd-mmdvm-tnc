#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "Run this bootstrap as root: sudo ./installer/bootstrap.sh" >&2
  exit 1
fi

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
EXPECTED_CORE="c28c46c3478d7931af611923c92cd8f692a00858"

echo "===== YWD-MMDVM-TNC BOOTSTRAP ====="

command -v apt-get >/dev/null 2>&1 || {
  echo "[FAIL] This bootstrap currently targets Raspberry Pi OS / Debian-family systems." >&2
  exit 2
}

export DEBIAN_FRONTEND=noninteractive
apt-get update

packages=(
  git
  python3
  python3-venv
  python3-pip
  psmisc
  make
  gcc-arm-none-eabi
  stm32flash
)

if apt-cache show raspi-utils >/dev/null 2>&1; then
  packages+=(raspi-utils)
fi

apt-get install -y "${packages[@]}"

for tool in git python3 fuser make arm-none-eabi-gcc arm-none-eabi-g++ arm-none-eabi-objcopy stm32flash; do
  command -v "$tool" >/dev/null 2>&1 || {
    echo "[FAIL] Required tool was not installed: $tool" >&2
    exit 3
  }
done

if [[ -r /proc/device-tree/model ]]; then
  model="$(tr -d '\0' </proc/device-tree/model)"
  echo "HOST_MODEL=$model"
  [[ "$model" == *"Raspberry Pi 5"* ]] || {
    echo "[FAIL] Qualified HAT GPIO control currently requires Raspberry Pi 5." >&2
    exit 4
  }
fi

git -c safe.directory='*' -C "$ROOT" submodule update --init --recursive
actual_core="$(git -c safe.directory='*' -C "$ROOT/vendor/ywd-1278" rev-parse HEAD)"
[[ "$actual_core" == "$EXPECTED_CORE" ]] || {
  echo "[FAIL] Qualified core mismatch: expected $EXPECTED_CORE got $actual_core" >&2
  exit 5
}

if ! command -v pinctrl >/dev/null 2>&1; then
  echo "[WARN] pinctrl is not installed. Runtime RX can still be installed, but qualified GPIO bootloader control/flash will not be available until pinctrl is present."
else
  echo "PINCTRL=$(command -v pinctrl)"
fi

bash "$ROOT/installer/install.sh"

echo
echo "YWD_TNC_BOOTSTRAP=PASS"
echo "AUTO_FLASH=NO"
echo "SERVICE_STARTED=NO"
echo "TX_ENABLED_BY_BOOTSTRAP=NO"
