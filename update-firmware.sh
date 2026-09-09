#!/usr/bin/env bash
set -Eeuo pipefail
SELF="$(readlink -f -- "${BASH_SOURCE[0]}")"
ROOT="$(cd -- "$(dirname -- "$SELF")" && pwd)"
exec "$ROOT/firmware/update.sh" "$@"
