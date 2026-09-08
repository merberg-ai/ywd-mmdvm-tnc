#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/installer/lib/ui.sh"
YWD_TNC_LOG_BASENAME=${YWD_TNC_LOG_BASENAME:-firmware-prepare}
ui_init "$@"

PROFILE="$ROOT/firmware/product-ax25r4.json"

[[ -f "$PROFILE" ]] || { ui_fail "Product firmware profile is missing."; exit 2; }

artifact_rel="$(python3 - "$PROFILE" <<'PY'
import json,sys
print(json.load(open(sys.argv[1], encoding='utf-8'))['artifact_relative_path'])
PY
)"
artifact="$ROOT/$artifact_rel"

verify_artifact() {
  env PYTHONPATH="$ROOT/src" python3 -m ywdtnc.firmware \
    --profile "$PROFILE" verify-artifact --firmware "$artifact" \
    >>"$YWD_TNC_LOG_FILE" 2>&1
}

ui_header "Preparing qualified HAT firmware"

if [[ -f "$artifact" ]] && verify_artifact; then
  ui_ok "Qualified firmware artifact already present and verified"
else
  if [[ -e "$artifact" ]]; then
    ui_warn "Existing firmware artifact did not verify; rebuilding from qualified in-repo source."
  else
    ui_step "Qualified firmware artifact is not present; building from qualified in-repo source"
  fi

  YWD_TNC_INSTALLER_BUILD=1 \
  YWD_TNC_LOG_FILE="$YWD_TNC_LOG_FILE" \
    "$ROOT/firmware/build.sh"

  verify_artifact || {
    ui_fail "Qualified firmware artifact still does not verify after build."
    exit 3
  }
  ui_ok "Qualified in-repo firmware artifact built and verified"
fi

ui_log "QUALIFIED_FIRMWARE_ARTIFACT=$artifact FIRMWARE_BUILD_SOURCE=IN_REPO"
printf 'Firmware artifact: %s\n' "$artifact"
