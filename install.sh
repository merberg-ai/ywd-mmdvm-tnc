#!/usr/bin/env bash
set -Eeuo pipefail

REPO_URL="${YWD_TNC_REPO_URL:-https://github.com/merberg-ai/ywd-mmdvm-tnc.git}"
REF="${YWD_TNC_REF:-main}"
TMP=""

c_reset='' c_cyan='' c_green='' c_red='' c_bold=''
if [[ -t 1 && -z "${NO_COLOR:-}" && "${TERM:-dumb}" != dumb ]]; then
  c_reset=$'\033[0m'; c_cyan=$'\033[36m'; c_green=$'\033[32m'; c_red=$'\033[31m'; c_bold=$'\033[1m'
fi

fail(){ printf '%s✗%s %s\n' "$c_red" "$c_reset" "$*" >&2; exit 1; }
step(){ printf '%s→%s %s\n' "$c_cyan" "$c_reset" "$*"; }
ok(){ printf '%s✓%s %s\n' "$c_green" "$c_reset" "$*"; }
cleanup(){ [[ -n "$TMP" ]] && rm -rf "$TMP"; }
trap cleanup EXIT

printf '\n%s%sYWD-MMDVM-TNC%s\n' "$c_bold" "$c_cyan" "$c_reset"
printf 'Guided Raspberry Pi installer\n\n'

[[ ${EUID:-$(id -u)} -eq 0 ]] || fail "Run the one-line installer through sudo (see README)."
command -v apt-get >/dev/null 2>&1 || fail "This installer currently supports Raspberry Pi OS / Debian-family systems."

if ! command -v git >/dev/null 2>&1; then
  step "Installing Git"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq >/dev/null
  apt-get install -y -qq git ca-certificates >/dev/null
  ok "Git installed"
fi

TMP="$(mktemp -d /tmp/ywd-mmdvm-tnc-bootstrap.XXXXXX)"
step "Downloading YWD-MMDVM-TNC ($REF)"
git clone --quiet --recursive --branch "$REF" --depth 1 "$REPO_URL" "$TMP/repo" || fail "Could not clone $REPO_URL at ref $REF"
ok "Source downloaded"

export YWD_TNC_BOOTSTRAP_SOURCE=curl
export YWD_TNC_SOURCE_REF="$REF"
"$TMP/repo/installer/setup.sh"
