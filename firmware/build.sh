#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/installer/lib/ui.sh"
YWD_TNC_LOG_BASENAME=firmware-build
ui_init "$@"

PROFILE="$ROOT/firmware/product-ax25r4.json"
BUILDER="$ROOT/firmware/build-qualified-inrepo.py"
TOOLCHAIN="$ROOT/firmware/tooling/qualified-toolchain.json"
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
[[ -f "$BUILDER" ]] || { ui_fail "Qualified in-repo firmware builder is missing."; exit 2; }
[[ -f "$TOOLCHAIN" ]] || { ui_fail "Qualified firmware toolchain manifest is missing."; exit 2; }
[[ -d "$ROOT/firmware/tooling" ]] || { ui_fail "Qualified firmware tooling is missing."; exit 2; }
[[ -d "$ROOT/firmware/vendor/ywd-mmdvm" ]] || { ui_fail "Qualified firmware engineering inputs are missing."; exit 2; }

ui_header "YWD-MMDVM-TNC firmware build"

readarray -t provenance < <(python3 - "$PROFILE" "$TOOLCHAIN" <<'PY'
import json, sys
profile = json.load(open(sys.argv[1], encoding='utf-8'))
toolchain = json.load(open(sys.argv[2], encoding='utf-8'))
print(profile['artifact_relative_path'])
print(toolchain['source_provenance']['qualified_builder_blob'])
print(toolchain['build_environment']['arm_gcc_full_version'])
print(toolchain['qualified_artifact']['size_bytes'])
print(toolchain['qualified_artifact']['sha256'])
PY
)
artifact_rel="${provenance[0]}"
expected_builder_blob="${provenance[1]}"
expected_gcc="${provenance[2]}"
expected_size="${provenance[3]}"
expected_sha="${provenance[4]}"
artifact="$ROOT/$artifact_rel"

actual_builder_blob="$(git hash-object "$BUILDER" 2>>"$YWD_TNC_LOG_FILE" || true)"
[[ "$actual_builder_blob" == "$expected_builder_blob" ]] || {
  ui_fail "Qualified in-repo firmware builder mismatch."
  ui_log "expected_builder_blob=$expected_builder_blob actual_builder_blob=${actual_builder_blob:-UNKNOWN}"
  exit 2
}
actual_gcc="$(arm-none-eabi-gcc -dumpfullversion 2>>"$YWD_TNC_LOG_FILE" || true)"
[[ "$actual_gcc" == "$expected_gcc" ]] || {
  ui_fail "Firmware toolchain mismatch: expected arm-none-eabi-gcc $expected_gcc, found ${actual_gcc:-UNKNOWN}."
  exit 2
}
ui_ok "Qualified in-repo firmware source and toolchain verified"

if [[ -z "$JOBS" ]]; then
  if command -v nproc >/dev/null 2>&1; then JOBS="$(nproc)"; else JOBS=1; fi
fi
[[ "$JOBS" =~ ^[0-9]+$ ]] && (( JOBS >= 1 )) || { ui_fail "YWD_TNC_FIRMWARE_BUILD_JOBS must be a positive integer."; exit 2; }

for tool in git make arm-none-eabi-gcc arm-none-eabi-g++ arm-none-eabi-objcopy python3 tar; do
  command -v "$tool" >/dev/null 2>&1 || { ui_fail "Missing build dependency: $tool"; exit 2; }
done

if [[ ${EUID:-$(id -u)} -eq 0 ]]; then
  build_user="${YWD_TNC_BUILD_USER:-${SUDO_USER:-}}"
  [[ -n "$build_user" && "$build_user" != root ]] || {
    ui_fail "Guided firmware build needs the original non-root sudo user. Re-run the installer from a normal account with sudo."
    exit 3
  }
  id "$build_user" >/dev/null 2>&1 || { ui_fail "Firmware build user does not exist: $build_user"; exit 3; }
  command -v sudo >/dev/null 2>&1 || { ui_fail "sudo is required for the guided non-root firmware build."; exit 3; }

  build_group="$(id -gn "$build_user")"
  BUILD_WORKSPACE="$(mktemp -d /tmp/ywd-mmdvm-tnc-fwbuild.XXXXXX)"
  build_root="$BUILD_WORKSPACE/ywd-mmdvm-tnc"
  mkdir -p "$build_root"

  ui_step "Preparing isolated non-root in-repo firmware build workspace"
  if ! tar -C "$ROOT" -cf "$BUILD_WORKSPACE/inrepo-firmware.tar" \
      firmware/build-qualified-inrepo.py \
      firmware/tooling \
      firmware/vendor \
      >>"$YWD_TNC_LOG_FILE" 2>&1; then
    ui_fail "Could not export the qualified in-repo firmware inputs."
    exit 4
  fi
  if ! tar -xf "$BUILD_WORKSPACE/inrepo-firmware.tar" -C "$build_root" >>"$YWD_TNC_LOG_FILE" 2>&1; then
    ui_fail "Could not prepare the in-repo firmware build workspace."
    exit 4
  fi
  rm -f "$BUILD_WORKSPACE/inrepo-firmware.tar"
  chown -R "$build_user:$build_group" "$BUILD_WORKSPACE"
  ui_ok "Non-root in-repo build workspace ready for $build_user"

  ui_run "Building the qualified AX.25 firmware twice from in-repo inputs" \
    sudo -H -u "$build_user" -- python3 "$build_root/firmware/build-qualified-inrepo.py" --jobs "$JOBS"

  built_artifact="$build_root/$artifact_rel"
  [[ -f "$built_artifact" ]] || { ui_fail "Non-root build completed without the expected firmware artifact."; exit 5; }
  mkdir -p "$(dirname "$artifact")"
  install -m 0644 "$built_artifact" "$artifact"
  built_metadata="$(dirname "$built_artifact")/build-metadata.json"
  if [[ -f "$built_metadata" ]]; then
    install -m 0644 "$built_metadata" "$(dirname "$artifact")/build-metadata.json"
  fi
  ui_ok "Build artifact returned to installer workspace"
else
  ui_run "Building the qualified AX.25 firmware twice from in-repo inputs" \
    python3 "$BUILDER" --jobs "$JOBS"
fi

ui_run "Verifying firmware size and SHA-256" env PYTHONPATH="$ROOT/src" python3 -m ywdtnc.firmware --profile "$PROFILE" verify-artifact --firmware "$artifact"
actual_size="$(stat -c %s "$artifact")"
actual_sha="$(sha256sum "$artifact" | awk '{print $1}')"
[[ "$actual_size" == "$expected_size" && "$actual_sha" == "$expected_sha" ]] || {
  ui_fail "Qualified firmware artifact changed after profile verification."
  exit 6
}

ui_header "Build complete"
printf 'Firmware: %s\n' "$artifact"
printf 'Log:      %s\n' "$YWD_TNC_LOG_FILE"
ui_log "FIRMWARE_BUILD_SOURCE=IN_REPO YWD1278_FIRMWARE_BUILDER_INVOKED=NO BUILD_EXECUTED_AS_ROOT=NO HARDWARE_ACCESS=NO GPIO_ACCESSED=NO FLASH_WRITTEN=NO RF_TRANSMITTED=NO"
