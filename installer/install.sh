#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "Run this installer as root (sudo)." >&2
  exit 1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
QUALIFIED_CORE="c28c46c3478d7931af611923c92cd8f692a00858"
PREFIX=/opt/ywd-mmdvm-tnc
SOURCE="$PREFIX/source"
VENV="$PREFIX/venv"
CONFIG_DIR=/etc/ywd-mmdvm-tnc
CONFIG="$CONFIG_DIR/config.toml"
SERVICE=/etc/systemd/system/ywd-mmdvm-tnc.service

if [[ -d "$ROOT/.git" || -f "$ROOT/.git" ]]; then
  git -C "$ROOT" submodule update --init --recursive
fi
if [[ ! -e "$ROOT/vendor/ywd-1278/src/ywd1278/__init__.py" ]]; then
  echo "Pinned vendor/ywd-1278 submodule is missing. Clone with --recursive or initialize submodules." >&2
  exit 1
fi
ACTUAL_CORE="$(git -C "$ROOT/vendor/ywd-1278" rev-parse HEAD)"
if [[ "$ACTUAL_CORE" != "$QUALIFIED_CORE" ]]; then
  echo "Refusing unqualified modem core: expected $QUALIFIED_CORE, got $ACTUAL_CORE" >&2
  exit 1
fi

command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }
python3 -m venv --help >/dev/null 2>&1 || { echo "python3-venv is required" >&2; exit 1; }

mkdir -p "$PREFIX" "$CONFIG_DIR"
rm -rf "$SOURCE"
mkdir -p "$SOURCE"
cp -a "$ROOT/." "$SOURCE/"

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

"$VENV/bin/ywd-tncd" --config "$CONFIG" --framework-self-test

echo
echo "YWD-MMDVM-TNC installed."
echo "Review $CONFIG, then enable/start with:"
echo "  sudo systemctl enable --now ywd-mmdvm-tnc.service"
echo "The installer does not flash firmware and does not start RF service automatically."
