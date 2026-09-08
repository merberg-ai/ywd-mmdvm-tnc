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
INSTALLER_BUILD="${YWD_TNC_INSTALLER_BUILD:-0}"
BUILD_WORKSPACE=""

cleanup_build_workspace() {
  if [[ -n "$BUILD_WORKSPACE" && -d "$BUILD_WORKSPACE" ]]; then
    rm -rf "$BUILD_WORKSPACE"
  fi
}
trap cleanup_build_workspace EXIT

if [[ ${EUID:-$(id -u)} -eq 0 && "$INSTALLER_BUILD" != 1 ]]; then
  ui_fail "Build firmware without sudo/root. The guided installer handles its own controlled non-root build."
  exit 2
fi
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

if [[ ${EUID:-$(id -u)} -eq 0 ]]; then
  build_user="${YWD_TNC_BUILD_USER:-${SUDO_USER:-}}"
  [[ -n "$build_user" && "$build_user" != root ]] || {
    ui_fail "Guided firmware build needs the original non-root sudo user. Re-run the installer from a normal account with sudo."
    exit 3
  }
  id "$build_user" >/dev/null 2>&1 || { ui_fail "Firmware build user does not exist: $build_user"; exit 3; }
  command -v sudo >/dev/null 2>&1 || { ui_fail "sudo is required for the guided non-root firmware build."; exit 3; }
  command -v tar >/dev/null 2>&1 || { ui_fail "tar is required for the guided firmware build."; exit 3; }

  build_group="$(id -gn "$build_user")"
  BUILD_WORKSPACE="$(mktemp -d /tmp/ywd-mmdvm-tnc-fwbuild.XXXXXX)"
  build_core="$BUILD_WORKSPACE/ywd-1278"
  mkdir -p "$build_core"

  ui_step "Preparing isolated non-root firmware build workspace"
  if ! git -c safe.directory='*' -C "$CORE" archive --format=tar HEAD >"$BUILD_WORKSPACE/core.tar" 2>>"$YWD_TNC_LOG_FILE"; then
    ui_fail "Could not export the pinned firmware source."
    exit 4
  fi
  if ! tar -xf "$BUILD_WORKSPACE/core.tar" -C "$build_core" >>"$YWD_TNC_LOG_FILE" 2>&1; then
    ui_fail "Could not prepare the firmware build workspace."
    exit 4
  fi
  rm -f "$BUILD_WORKSPACE/core.tar"
  chown -R "$build_user:$build_group" "$BUILD_WORKSPACE"
  ui_ok "Non-root build workspace ready for $build_user"

  ui_run "Building the qualified AX.25 firmware twice (reproducibility check)" \
    sudo -H -u "$build_user" -- python3 "$build_core/firmware/build-packet-rssi-ywd1278.py" --jobs "$JOBS"

  core_artifact_rel="${artifact_rel#vendor/ywd-1278/}"
  built_artifact="$build_core/$core_artifact_rel"
  [[ -f "$built_artifact" ]] || { ui_fail "Non-root build completed without the expected firmware artifact."; exit 5; }
  mkdir -p "$(dirname "$artifact")"
  install -m 0644 "$built_artifact" "$artifact"
  ui_ok "Build artifact returned to installer workspace"
else
  ui_run "Building the qualified AX.25 firmware twice (reproducibility check)" \
    python3 "$CORE/firmware/build-packet-rssi-ywd1278.py" --jobs "$JOBS"
fi

ui_run "Verifying firmware size and SHA-256" env PYTHONPATH="$ROOT/src" python3 -m ywdtnc.firmware --profile "$PROFILE" verify-artifact --firmware "$artifact"

ui_header "Build complete"
printf 'Firmware: %s\n' "$artifact"
printf 'Log:      %s\n' "$YWD_TNC_LOG_FILE"
ui_log "BUILD_EXECUTED_AS_ROOT=NO HARDWARE_ACCESS=NO GPIO_ACCESSED=NO FLASH_WRITTEN=NO RF_TRANSMITTED=NO"
