#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "Run as root: sudo ./installer/install.sh" >&2
  exit 1
fi

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/installer/lib/ui.sh"
YWD_TNC_LOG_BASENAME=${YWD_TNC_LOG_BASENAME:-install}
ui_init "$@"

QUALIFIED_CORE="c28c46c3478d7931af611923c92cd8f692a00858"
PREFIX=/opt/ywd-mmdvm-tnc
SOURCE="$PREFIX/source"
VENV="$PREFIX/venv"
CONFIG_DIR=/etc/ywd-mmdvm-tnc
CONFIG="$CONFIG_DIR/config.toml"
STATE_DIR=/var/lib/ywd-mmdvm-tnc
SERVICE=/etc/systemd/system/ywd-mmdvm-tnc.service

ui_header "Installing YWD-MMDVM-TNC"

if [[ -d "$ROOT/.git" || -f "$ROOT/.git" ]]; then
  ui_run "Synchronizing qualified modem core" git -c safe.directory='*' -C "$ROOT" submodule update --init --recursive
fi
[[ -e "$ROOT/vendor/ywd-1278/src/ywd1278/__init__.py" ]] || {
  ui_fail "Qualified modem core is missing. Clone recursively or run installer/bootstrap.sh first."
  exit 2
}
actual_core="$(git -c safe.directory='*' -C "$ROOT/vendor/ywd-1278" rev-parse HEAD 2>>"$YWD_TNC_LOG_FILE" || true)"
[[ "$actual_core" == "$QUALIFIED_CORE" ]] || {
  ui_fail "Refusing an unqualified modem core."
  ui_log "expected_core=$QUALIFIED_CORE actual_core=${actual_core:-UNKNOWN}"
  exit 3
}

command -v python3 >/dev/null 2>&1 || { ui_fail "python3 is required"; exit 4; }
python3 - <<'PY' >>"$YWD_TNC_LOG_FILE" 2>&1 || { ui_fail "Python 3.11 or newer is required"; exit 4; }
import sys
raise SystemExit(0 if sys.version_info >= (3,11) else 1)
PY
ui_ok "Qualified source and Python runtime verified"

ui_run "Checking product configuration contract" env PYTHONPATH="$ROOT/src:$ROOT/vendor/ywd-1278/src" python3 -m ywdtnc.tncd --config "$ROOT/config/ywd-mmdvm-tnc.example.toml" --framework-self-test

install -d -m 0755 "$PREFIX" "$CONFIG_DIR"
install -d -m 0750 "$STATE_DIR"
rm -rf "$SOURCE"
mkdir -p "$SOURCE"
ui_step "Installing product files"
cp -a "$ROOT/." "$SOURCE/" >>"$YWD_TNC_LOG_FILE" 2>&1
ui_ok "Product files installed"

rm -rf "$VENV"
ui_run "Creating isolated Python environment" python3 -m venv "$VENV"
ui_run "Installing YWD-MMDVM-TNC package" bash -c '"$1/bin/python" -m pip install --upgrade pip setuptools wheel && "$1/bin/python" -m pip install --no-deps "$2/vendor/ywd-1278" && "$1/bin/python" -m pip install --no-deps "$2"' _ "$VENV" "$SOURCE"

if [[ ! -e "$CONFIG" ]]; then
  install -m 0640 "$SOURCE/config/ywd-mmdvm-tnc.example.toml" "$CONFIG"
  ui_ok "Safe default configuration created"
else
  ui_ok "Existing configuration preserved"
fi

install -m 0644 "$SOURCE/systemd/ywd-mmdvm-tnc.service" "$SERVICE"
ui_run "Registering system service" systemctl daemon-reload

ln -sfn "$VENV/bin/ywd-tncd" /usr/local/bin/ywd-tncd
ln -sfn "$VENV/bin/ywd-tnc-fw" /usr/local/bin/ywd-tnc-fw
ln -sfn "$VENV/bin/ywd-tnc-rx-gate" /usr/local/bin/ywd-tnc-rx-gate
ln -sfn "$VENV/bin/ywd-tnc-p2-gate" /usr/local/bin/ywd-tnc-p2-gate 2>/dev/null || true

ui_run "Validating installed service configuration" "$VENV/bin/ywd-tncd" --config "$CONFIG" --framework-self-test
ui_ok "YWD-MMDVM-TNC installed"
ui_log "QUALIFIED_CORE_COMMIT=$QUALIFIED_CORE CONFIG=$CONFIG SERVICE=$SERVICE"
printf 'Log: %s\n' "$YWD_TNC_LOG_FILE"
