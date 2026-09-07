#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "Run this installer as root: sudo ./installer/install.sh" >&2
  exit 1
fi

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
QUALIFIED_CORE="c28c46c3478d7931af611923c92cd8f692a00858"
PREFIX=/opt/ywd-mmdvm-tnc
SOURCE="$PREFIX/source"
VENV="$PREFIX/venv"
CONFIG_DIR=/etc/ywd-mmdvm-tnc
CONFIG="$CONFIG_DIR/config.toml"
STATE_DIR=/var/lib/ywd-mmdvm-tnc
SERVICE=/etc/systemd/system/ywd-mmdvm-tnc.service

echo "===== YWD-MMDVM-TNC INSTALL ====="

if [[ -d "$ROOT/.git" || -f "$ROOT/.git" ]]; then
  git -c safe.directory='*' -C "$ROOT" submodule update --init --recursive
fi
if [[ ! -e "$ROOT/vendor/ywd-1278/src/ywd1278/__init__.py" ]]; then
  echo "[FAIL] Pinned vendor/ywd-1278 submodule is missing. Clone with --recursive or initialize submodules." >&2
  exit 2
fi
ACTUAL_CORE="$(git -c safe.directory='*' -C "$ROOT/vendor/ywd-1278" rev-parse HEAD 2>/dev/null || true)"
if [[ "$ACTUAL_CORE" != "$QUALIFIED_CORE" ]]; then
  echo "[FAIL] Refusing unqualified modem core: expected $QUALIFIED_CORE, got ${ACTUAL_CORE:-UNKNOWN}" >&2
  exit 3
fi

command -v python3 >/dev/null || { echo "[FAIL] python3 is required" >&2; exit 4; }
python3 - <<'PY' || { echo "[FAIL] Python 3.11 or newer is required" >&2; exit 4; }
import sys
raise SystemExit(0 if sys.version_info >= (3,11) else 1)
PY

PYTHONPATH="$ROOT/src:$ROOT/vendor/ywd-1278/src" \
  python3 -m ywdtnc.firmware --profile "$ROOT/firmware/product-ax25r4.json" show
PYTHONPATH="$ROOT/src:$ROOT/vendor/ywd-1278/src" \
  python3 -m ywdtnc.tncd --config "$ROOT/config/ywd-mmdvm-tnc.example.toml" --framework-self-test

install -d -m 0755 "$PREFIX" "$CONFIG_DIR"
install -d -m 0750 "$STATE_DIR"
rm -rf "$SOURCE"
mkdir -p "$SOURCE"
cp -a "$ROOT/." "$SOURCE/"

rm -rf "$VENV"
python3 -m venv "$VENV"
"$VENV/bin/python" -m pip install --upgrade pip setuptools wheel
"$VENV/bin/python" -m pip install --no-deps "$SOURCE/vendor/ywd-1278"
"$VENV/bin/python" -m pip install --no-deps "$SOURCE"

if [[ ! -e "$CONFIG" ]]; then
  install -m 0640 "$SOURCE/config/ywd-mmdvm-tnc.example.toml" "$CONFIG"
  echo "Created safe TX-disabled config: $CONFIG"
else
  echo "Preserved existing config: $CONFIG"
fi

install -m 0644 "$SOURCE/systemd/ywd-mmdvm-tnc.service" "$SERVICE"
systemctl daemon-reload

ln -sfn "$VENV/bin/ywd-tncd" /usr/local/bin/ywd-tncd
ln -sfn "$VENV/bin/ywd-tnc-fw" /usr/local/bin/ywd-tnc-fw
ln -sfn "$VENV/bin/ywd-tnc-rx-gate" /usr/local/bin/ywd-tnc-rx-gate

"$VENV/bin/ywd-tncd" --config "$CONFIG" --framework-self-test

tx_enabled="$("$VENV/bin/python" - "$CONFIG" <<'PY'
import sys,tomllib
with open(sys.argv[1], "rb") as f:
    data=tomllib.load(f)
print("true" if data.get("radio",{}).get("tx_enabled") is True else "false")
PY
)"
if [[ "$tx_enabled" == true ]]; then
  echo "[WARN] Existing configuration has radio.tx_enabled=true. The installer did NOT start the service."
fi

echo
echo "YWD_TNC_INSTALL=PASS"
echo "QUALIFIED_CORE_COMMIT=$QUALIFIED_CORE"
echo "CONFIG=$CONFIG"
echo "SERVICE_INSTALLED=YES"
echo "SERVICE_STARTED=NO"
echo "AUTO_FLASH=NO"
echo "RF_TRANSMITTED=NO"
echo
echo "Next:"
echo "  sudo ./firmware/probe.sh"
echo "  sudo systemctl start ywd-mmdvm-tnc.service"
echo "  ywd-tnc-rx-gate --timeout 120"
