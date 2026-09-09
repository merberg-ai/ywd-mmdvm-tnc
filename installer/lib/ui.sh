#!/usr/bin/env bash
# Shared terminal UI + logging helpers for YWD-MMDVM-TNC maintenance scripts.

YWD_TNC_UI_INITIALIZED=${YWD_TNC_UI_INITIALIZED:-0}
YWD_TNC_SPINNER_PID=""

_ui_color_enabled() {
  [[ -t 1 && "${NO_COLOR:-}" == "" && "${TERM:-dumb}" != "dumb" ]]
}

_ui_set_colors() {
  if _ui_color_enabled; then
    UI_RESET=$'\033[0m'
    UI_BOLD=$'\033[1m'
    UI_DIM=$'\033[2m'
    UI_CYAN=$'\033[36m'
    UI_GREEN=$'\033[32m'
    UI_YELLOW=$'\033[33m'
    UI_RED=$'\033[31m'
    UI_BLUE=$'\033[34m'
  else
    UI_RESET="" UI_BOLD="" UI_DIM="" UI_CYAN="" UI_GREEN="" UI_YELLOW="" UI_RED="" UI_BLUE=""
  fi
}

ui_init() {
  _ui_set_colors
  local log_dir stamp default_log
  if [[ ${EUID:-$(id -u)} -eq 0 ]]; then
    log_dir=/var/log/ywd-mmdvm-tnc
  else
    log_dir="${XDG_STATE_HOME:-$HOME/.local/state}/ywd-mmdvm-tnc"
  fi
  mkdir -p "$log_dir"
  stamp="$(date +%Y%m%d-%H%M%S)"
  default_log="$log_dir/${YWD_TNC_LOG_BASENAME:-operation}-$stamp.log"
  YWD_TNC_LOG_FILE="${YWD_TNC_LOG_FILE:-$default_log}"
  touch "$YWD_TNC_LOG_FILE"
  chmod 0640 "$YWD_TNC_LOG_FILE" 2>/dev/null || true
  YWD_TNC_UI_INITIALIZED=1
  export YWD_TNC_LOG_FILE
  {
    printf '\n===== %s =====\n' "$(date -Is)"
    printf 'command=%q' "$0"
    printf ' %q' "$@"
    printf '\n'
  } >>"$YWD_TNC_LOG_FILE"
}

ui_log() {
  [[ "$YWD_TNC_UI_INITIALIZED" == 1 ]] || ui_init
  printf '[%s] %s\n' "$(date -Is)" "$*" >>"$YWD_TNC_LOG_FILE"
}

ui_header() {
  printf '\n%s%s%s\n' "$UI_BOLD$UI_CYAN" "$*" "$UI_RESET"
  printf '%*s\n' "${#1}" '' | tr ' ' '-'
  ui_log "HEADER: $*"
}

ui_step() {
  printf '%s→%s %s\n' "$UI_CYAN" "$UI_RESET" "$*"
  ui_log "STEP: $*"
}

ui_ok() {
  printf '%s✓%s %s\n' "$UI_GREEN" "$UI_RESET" "$*"
  ui_log "OK: $*"
}

ui_warn() {
  printf '%s!%s %s\n' "$UI_YELLOW" "$UI_RESET" "$*" >&2
  ui_log "WARN: $*"
}

ui_fail() {
  printf '%s✗%s %s\n' "$UI_RED" "$UI_RESET" "$*" >&2
  printf '%sLog:%s %s\n' "$UI_DIM" "$UI_RESET" "$YWD_TNC_LOG_FILE" >&2
  ui_log "FAIL: $*"
}

_ui_terminal_cols() {
  local cols=""
  if command -v tput >/dev/null 2>&1 && [[ "${TERM:-dumb}" != "dumb" ]]; then
    cols="$(tput cols 2>/dev/null || true)"
  fi
  if [[ ! "$cols" =~ ^[0-9]+$ || "$cols" -lt 8 ]]; then
    cols="${COLUMNS:-80}"
  fi
  if [[ ! "$cols" =~ ^[0-9]+$ || "$cols" -lt 8 ]]; then
    cols=80
  fi
  printf '%s' "$cols"
}

_ui_fit_spinner_label() {
  local label=$1 cols=${2:-80} max
  [[ "$cols" =~ ^[0-9]+$ ]] || cols=80
  (( cols >= 8 )) || cols=8
  # Reserve four columns for the spinner, separating space and wrap safety.
  # Keeping the transient frame below the terminal width prevents terminals
  # such as Termius from wrapping a carriage-return spinner onto new rows.
  max=$((cols - 4))
  if (( ${#label} <= max )); then
    printf '%s' "$label"
  elif (( max <= 3 )); then
    printf '%.*s' "$max" "$label"
  else
    printf '%.*s...' "$((max - 3))" "$label"
  fi
}

_ui_spinner() {
  local pid=$1 label=$2 delay=0.1 frames='|/-\\' i=0 cols fitted
  while kill -0 "$pid" 2>/dev/null; do
    cols="$(_ui_terminal_cols)"
    fitted="$(_ui_fit_spinner_label "$label" "$cols")"
    printf '\r\033[K%s%s%s %s' "$UI_CYAN" "${frames:i++%4:1}" "$UI_RESET" "$fitted"
    sleep "$delay"
  done
  printf '\r\033[K'
}

ui_run() {
  local label=$1
  shift
  ui_log "RUN: $label :: $*"
  if [[ -t 1 ]]; then
    ( "$@" ) >>"$YWD_TNC_LOG_FILE" 2>&1 &
    local pid=$!
    _ui_spinner "$pid" "$label"
    if wait "$pid"; then
      ui_ok "$label"
      return 0
    fi
  else
    if ( "$@" ) >>"$YWD_TNC_LOG_FILE" 2>&1; then
      ui_ok "$label"
      return 0
    fi
  fi
  ui_fail "$label"
  return 1
}

ui_run_capture() {
  local __var=$1 label=$2
  shift 2
  local out
  ui_log "RUN_CAPTURE: $label :: $*"
  if out="$("$@" 2>>"$YWD_TNC_LOG_FILE")"; then
    printf -v "$__var" '%s' "$out"
    ui_ok "$label"
    return 0
  fi
  ui_fail "$label"
  return 1
}

ui_prompt_yes_no() {
  local __var=$1 prompt=$2 default=${3:-no} answer suffix
  if [[ "$default" == yes ]]; then suffix='[Y/n]'; else suffix='[y/N]'; fi
  if [[ ! -r /dev/tty ]]; then
    ui_fail "Interactive setup requires a terminal (/dev/tty)."
    return 2
  fi
  printf '%s?%s %s %s ' "$UI_BLUE" "$UI_RESET" "$prompt" "$suffix" >/dev/tty
  IFS= read -r answer </dev/tty || answer=""
  answer=${answer,,}
  if [[ -z "$answer" ]]; then answer=$default; fi
  case "$answer" in
    y|yes) printf -v "$__var" '%s' yes ;;
    n|no) printf -v "$__var" '%s' no ;;
    *) ui_warn "Please answer yes or no."; ui_prompt_yes_no "$__var" "$prompt" "$default" ;;
  esac
}

ui_prompt_value() {
  local __var=$1 prompt=$2 default=$3 value
  if [[ ! -r /dev/tty ]]; then
    ui_fail "Interactive setup requires a terminal (/dev/tty)."
    return 2
  fi
  printf '%s?%s %s [%s]: ' "$UI_BLUE" "$UI_RESET" "$prompt" "$default" >/dev/tty
  IFS= read -r value </dev/tty || value=""
  [[ -n "$value" ]] || value=$default
  printf -v "$__var" '%s' "$value"
}

ui_prompt_access() {
  local __var=$1 answer
  if [[ ! -r /dev/tty ]]; then
    ui_fail "Interactive setup requires a terminal (/dev/tty)."
    return 2
  fi
  printf '\n%sKISS network access%s\n' "$UI_BOLD" "$UI_RESET" >/dev/tty
  printf '  1) Local only  — 127.0.0.1 (recommended default)\n' >/dev/tty
  printf '  2) LAN access  — 0.0.0.0 (trusted LAN only)\n' >/dev/tty
  printf '%s?%s Select [1]: ' "$UI_BLUE" "$UI_RESET" >/dev/tty
  IFS= read -r answer </dev/tty || answer=""
  case "${answer:-1}" in
    1) printf -v "$__var" '%s' local ;;
    2) printf -v "$__var" '%s' lan ;;
    *) ui_warn "Please choose 1 or 2."; ui_prompt_access "$__var" ;;
  esac
}

ui_log_tail() {
  local lines=${1:-20}
  printf '\n%sLast log lines:%s\n' "$UI_DIM" "$UI_RESET" >&2
  tail -n "$lines" "$YWD_TNC_LOG_FILE" >&2 || true
}
