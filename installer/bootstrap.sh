#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "Run as root: sudo ./installer/bootstrap.sh" >&2
  exit 1
fi

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/installer/lib/ui.sh"
YWD_TNC_LOG_BASENAME=${YWD_TNC_LOG_BASENAME:-bootstrap}
ui_init "$@"

EXPECTED_CORE="c28c46c3478d7931af611923c92cd8f692a00858"
DEPENDENCIES_ONLY=0
[[ "${1:-}" == "--dependencies-only" ]] && DEPENDENCIES_ONLY=1

ui_header "YWD-MMDVM-TNC system preparation"
command -v apt-get >/dev/null 2>&1 || { ui_fail "Raspberry Pi OS / Debian-family system required."; exit 2; }

export DEBIAN_FRONTEND=noninteractive
ui_run "Refreshing package index" apt-get update

packages=(git python3 python3-venv python3-pip psmisc make gcc-arm-none-eabi stm32flash iproute2 ca-certificates curl)
if apt-cache show raspi-utils >/dev/null 2>&1; then packages+=(raspi-utils); fi
ui_run "Installing required packages" apt-get install -y "${packages[@]}"

for tool in git python3 fuser make arm-none-eabi-gcc arm-none-eabi-g++ arm-none-eabi-objcopy stm32flash ss; do
  command -v "$tool" >/dev/null 2>&1 || { ui_fail "Required tool is unavailable after installation: $tool"; exit 3; }
done
ui_ok "Runtime and firmware tools available"

if [[ -r /proc/device-tree/model ]]; then
  model="$(tr -d '\0' </proc/device-tree/model)"
  ui_log "HOST_MODEL=$model"
  [[ "$model" == *"Raspberry Pi 5"* ]] || {
    ui_fail "Qualified HAT GPIO control currently requires Raspberry Pi 5 (found: $model)."
    exit 4
  }
  ui_ok "Qualified Raspberry Pi 5 host detected"
fi

if [[ -d "$ROOT/.git" || -f "$ROOT/.git" ]]; then
  ui_run "Initializing qualified modem core" git -c safe.directory='*' -C "$ROOT" submodule update --init --recursive
fi
actual_core="$(git -c safe.directory='*' -C "$ROOT/vendor/ywd-1278" rev-parse HEAD 2>>"$YWD_TNC_LOG_FILE" || true)"
[[ "$actual_core" == "$EXPECTED_CORE" ]] || {
  ui_fail "Qualified modem core mismatch."
  ui_log "expected_core=$EXPECTED_CORE actual_core=${actual_core:-UNKNOWN}"
  exit 5
}
ui_ok "Qualified modem core verified"

if command -v pinctrl >/dev/null 2>&1; then
  ui_ok "HAT GPIO control available"
else
  ui_warn "pinctrl is unavailable; qualified firmware flashing cannot run until raspi-utils provides it."
fi

if (( DEPENDENCIES_ONLY )); then
  ui_ok "System preparation complete"
  exit 0
fi

"$ROOT/installer/install.sh"
printf 'Log: %s\n' "$YWD_TNC_LOG_FILE"
