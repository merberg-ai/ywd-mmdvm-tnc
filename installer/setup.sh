#!/usr/bin/env bash
set -Eeuo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "Run as root: sudo ./installer/setup.sh" >&2
  exit 1
fi

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/installer/lib/ui.sh"
YWD_TNC_LOG_BASENAME=install
ui_init "$@"

CONFIG=/etc/ywd-mmdvm-tnc/config.toml
SERVICE=ywd-mmdvm-tnc.service
PACKETLOG_SERVICE=ywd-packetlog.service
EXPECTED_IDENTITY="MMDVM_HS_Hat-YWD-1278-AX25R4-v0.1.0-alpha1 14.7456MHz ADF7021 FW based on CA6JAU GitID #7ff74ed"
HAD_CONFIG=0
[[ -e "$CONFIG" ]] && HAD_CONFIG=1
PACKETLOG_WAS_ENABLED=0
PACKETLOG_WAS_ACTIVE=0
systemctl is-enabled --quiet "$PACKETLOG_SERVICE" 2>/dev/null && PACKETLOG_WAS_ENABLED=1 || true
systemctl is-active --quiet "$PACKETLOG_SERVICE" 2>/dev/null && PACKETLOG_WAS_ACTIVE=1 || true

on_error() {
  local rc=$?
  ui_fail "Setup stopped before completion (exit $rc)."
  ui_log_tail 25
  exit "$rc"
}
trap on_error ERR

ui_header "YWD-MMDVM-TNC guided setup"
printf '1200-baud AX.25 TCP KISS modem for the qualified MMDVM_HS HAT\n\n'
ui_log "bootstrap_source=${YWD_TNC_BOOTSTRAP_SOURCE:-manual} source_ref=${YWD_TNC_SOURCE_REF:-unknown}"

if systemctl is-active --quiet "$SERVICE" 2>/dev/null; then
  ui_step "Stopping the existing YWD-MMDVM-TNC service for maintenance"
  systemctl stop "$SERVICE" >>"$YWD_TNC_LOG_FILE" 2>&1
  ui_ok "Modem UART released"
fi

YWD_TNC_LOG_FILE="$YWD_TNC_LOG_FILE" "$ROOT/installer/bootstrap.sh" --dependencies-only
YWD_TNC_LOG_FILE="$YWD_TNC_LOG_FILE" "$ROOT/installer/install.sh"

configure=yes
if (( HAD_CONFIG )); then
  ui_prompt_yes_no configure "An existing YWD-MMDVM-TNC configuration was found. Reconfigure it?" no
fi

if [[ "$configure" == yes ]]; then
  ui_header "TNC configuration"
  ui_prompt_access access
  ui_prompt_value kiss_port "TCP KISS port" "8001"
  [[ "$kiss_port" =~ ^[0-9]+$ ]] && (( kiss_port >= 1 && kiss_port <= 65535 )) || {
    ui_fail "KISS port must be 1..65535."; exit 10;
  }

  ui_prompt_yes_no tx_answer "Enable RF transmit?" no
  if [[ "$tx_answer" == yes ]]; then
    frequency="145.050"
    ui_warn "TX is currently qualified only at 145.050 MHz with power setting 200."
    ui_prompt_yes_no tx_confirm "Use the qualified 145.050 MHz / power-200 TX profile?" yes
    [[ "$tx_confirm" == yes ]] || { ui_fail "TX configuration cancelled."; exit 11; }
    tx_enabled=true
  else
    ui_prompt_value frequency "Receive frequency in MHz" "145.050"
    tx_enabled=false
  fi

  ui_prompt_yes_no agw_answer "Enable the optional local AGW raw listener on 127.0.0.1:8000?" no
  if [[ "$agw_answer" == yes ]]; then agw_enabled=true; else agw_enabled=false; fi

  if [[ "$access" == lan ]]; then
    kiss_listen="0.0.0.0"
    wildcard=true
    ui_warn "LAN mode exposes unauthenticated KISS on every IPv4 interface. Keep TCP/$kiss_port on a trusted LAN/firewall."
  else
    kiss_listen="127.0.0.1"
    wildcard=false
  fi

  if [[ -e "$CONFIG" ]]; then
    backup="$CONFIG.$(date +%Y%m%d-%H%M%S).bak"
    cp -a "$CONFIG" "$backup"
    ui_log "CONFIG_BACKUP=$backup"
  fi

  ui_step "Writing YWD-MMDVM-TNC configuration"
  cat >"$CONFIG" <<EOF_CONFIG
# YWD-MMDVM-TNC configuration

[hardware]
target = "mmdvm-hs-hat-stm32f103-simplex-14.7456-adf7021"

[radio]
device = "/dev/ttyAMA0"
frequency_mhz = $frequency
tx_power = 200
tx_enabled = $tx_enabled

[packet]
baud = 1200
txdelay_ms = 300
persist = 63
slottime_ms = 100

[kiss]
enabled = true
listen = "$kiss_listen"
port = $kiss_port
allow_wildcard_bind = $wildcard

[agw]
enabled = $agw_enabled
listen = "127.0.0.1"
port = 8000
allow_wildcard_bind = false
raw_only = true

# Passive, live-only newline-delimited JSON event stream. This interface has no
# command or transmit API and is intentionally bound to loopback by default.
[monitor]
enabled = true
listen = "127.0.0.1"
port = 8002
allow_wildcard_bind = false

[firmware]
# Immutable identity of the physically-qualified firmware image.
required_identity = "$EXPECTED_IDENTITY"
allow_automatic_flash = false
EOF_CONFIG
  chmod 0640 "$CONFIG"
  ui_run "Validating configuration" /opt/ywd-mmdvm-tnc/venv/bin/ywd-tncd --config "$CONFIG" --framework-self-test
  ui_ok "Configuration saved"
else
  ui_ok "Existing configuration retained"
fi

monitor_state="$(/opt/ywd-mmdvm-tnc/venv/bin/python - "$CONFIG" <<'PY'
import sys, tomllib
with open(sys.argv[1], 'rb') as f:
    c = tomllib.load(f)
m = c.get('monitor')
if not isinstance(m, dict):
    print('missing')
elif m.get('enabled') is True:
    print('enabled')
else:
    print('disabled')
PY
)"

if [[ "$monitor_state" == missing ]]; then
  ui_header "Passive monitor stream"
  monitor_default=no
  if (( PACKETLOG_WAS_ENABLED || PACKETLOG_WAS_ACTIVE )); then
    monitor_default=yes
    ui_warn "An existing ywd-packetlog service was detected; enabling the product monitor stream allows it to migrate from KISS sniffing to first-class TNC events."
  fi
  ui_prompt_yes_no monitor_upgrade "Add the local passive monitor stream on 127.0.0.1:8002 without changing your existing radio/KISS settings?" "$monitor_default"
  if [[ "$monitor_upgrade" == yes ]]; then
    backup="$CONFIG.$(date +%Y%m%d-%H%M%S).before-monitor.bak"
    cp -a "$CONFIG" "$backup"
    cat >>"$CONFIG" <<'EOF_MONITOR'

# Passive, live-only newline-delimited JSON event stream. This interface has no
# command or transmit API and is intentionally bound to loopback by default.
[monitor]
enabled = true
listen = "127.0.0.1"
port = 8002
allow_wildcard_bind = false
EOF_MONITOR
    chmod 0640 "$CONFIG"
    ui_run "Validating monitor-upgraded configuration" /opt/ywd-mmdvm-tnc/venv/bin/ywd-tncd --config "$CONFIG" --framework-self-test
    ui_ok "Passive monitor stream added; existing TNC settings preserved"
    ui_log "MONITOR_CONFIG_BACKUP=$backup"
    monitor_state=enabled
  else
    ui_ok "Existing configuration left unchanged; passive monitor remains disabled"
  fi
fi

ui_header "HAT firmware"
ui_prompt_yes_no firmware_answer "Back up/verify the HAT and install the qualified packet firmware if needed?" yes
firmware_ready=no
if [[ "$firmware_answer" == yes ]]; then
  printf '%s\n' "The installer verifies or reproducibly builds the exact qualified firmware image first."
  printf '%s\n' "The firmware tool preserves a verified stock rollback image before any write."
  printf '%s\n' "A real flash write still requires typing WRITE-FIRMWARE-NOW."

  if ! YWD_TNC_LOG_FILE="$YWD_TNC_LOG_FILE" bash "$ROOT/firmware/ensure.sh"; then
    ui_fail "Qualified firmware could not be prepared."
    exit 19
  fi

  if [[ ! -r /dev/tty ]]; then
    ui_fail "Interactive firmware confirmation requires a controlling terminal (/dev/tty)."
    exit 20
  fi
  if YWD_TNC_LOG_FILE="$YWD_TNC_LOG_FILE" "$ROOT/firmware/flash.sh" flash --authorize FLASH-QUALIFIED-AX25R4 </dev/tty; then
    firmware_ready=yes
  else
    ui_fail "Firmware preparation failed."
    exit 20
  fi
else
  ui_warn "Firmware setup skipped. Verifying that the exact qualified image is already running."
  if YWD_TNC_LOG_FILE="$YWD_TNC_LOG_FILE" "$ROOT/firmware/probe.sh"; then
    firmware_ready=yes
  fi
fi

packetlog_state=disabled
if [[ "$firmware_ready" != yes ]]; then
  ui_warn "The service will not be started until the qualified firmware is installed."
else
  ui_header "Starting service"
  ui_run "Enabling YWD-MMDVM-TNC at boot" systemctl enable "$SERVICE"
  ui_run "Starting YWD-MMDVM-TNC" systemctl restart "$SERVICE"
  sleep 0.5
  systemctl is-active --quiet "$SERVICE" || {
    journalctl -u "$SERVICE" -n 60 --no-pager >>"$YWD_TNC_LOG_FILE" 2>&1 || true
    ui_fail "Service did not remain active."
    exit 30
  }
  ui_ok "YWD-MMDVM-TNC is running"

  if [[ "$monitor_state" == enabled ]]; then
    if (( PACKETLOG_WAS_ENABLED || PACKETLOG_WAS_ACTIVE )); then
      ui_header "Packet activity logger"
      if (( PACKETLOG_WAS_ENABLED )); then
        ui_run "Keeping ywd-packetlog enabled at boot" systemctl enable "$PACKETLOG_SERVICE"
      fi
      ui_run "Migrating/restarting ywd-packetlog on the product monitor stream" systemctl restart "$PACKETLOG_SERVICE"
      sleep 0.25
      systemctl is-active --quiet "$PACKETLOG_SERVICE" || {
        journalctl -u "$PACKETLOG_SERVICE" -n 40 --no-pager >>"$YWD_TNC_LOG_FILE" 2>&1 || true
        ui_fail "Packet logger did not remain active."
        exit 31
      }
      ui_ok "Existing packet logger migrated to the first-class monitor stream"
      packetlog_state=enabled
    else
      ui_prompt_yes_no packetlog_answer "Enable the passive packet activity logger at boot?" no
      if [[ "$packetlog_answer" == yes ]]; then
        ui_run "Enabling and starting ywd-packetlog" systemctl enable --now "$PACKETLOG_SERVICE"
        sleep 0.25
        systemctl is-active --quiet "$PACKETLOG_SERVICE" || {
          journalctl -u "$PACKETLOG_SERVICE" -n 40 --no-pager >>"$YWD_TNC_LOG_FILE" 2>&1 || true
          ui_fail "Packet logger did not remain active."
          exit 31
        }
        ui_ok "Passive packet activity logging enabled"
        packetlog_state=enabled
      else
        ui_ok "Packet logger installed but left disabled"
      fi
    fi
  elif (( PACKETLOG_WAS_ENABLED || PACKETLOG_WAS_ACTIVE )); then
    ui_warn "ywd-packetlog was previously configured, but the product monitor stream is disabled. Enable [monitor] before restarting the installed packet logger."
  fi
fi

trap - ERR
ui_header "Setup complete"

summary="$(/opt/ywd-mmdvm-tnc/venv/bin/python - "$CONFIG" "$packetlog_state" <<'PY'
import sys,tomllib
with open(sys.argv[1], 'rb') as f: c=tomllib.load(f)
r=c['radio']; k=c['kiss']; a=c['agw']; m=c.get('monitor', {})
print(f"frequency={r['frequency_mhz']}")
print(f"tx={'enabled' if r['tx_enabled'] else 'disabled'}")
print(f"kiss={k['listen']}:{k['port']}")
print(f"agw={'enabled' if a['enabled'] else 'disabled'}")
if isinstance(m, dict) and m.get('enabled') is True:
    print(f"monitor={m.get('listen', '127.0.0.1')}:{m.get('port', 8002)}")
else:
    print('monitor=disabled')
print(f"packetlog={sys.argv[2]}")
PY
)"
printf '%s\n' "$summary"
if grep -q '^kiss=0\.0\.0\.0:' <<<"$summary"; then
  lan_ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  [[ -n "$lan_ip" ]] && printf 'LAN clients: connect to %s:%s\n' "$lan_ip" "$(awk -F: '/^kiss=/{print $2}' <<<"$summary")"
fi
printf 'service=%s\n' "$SERVICE"
printf 'config=%s\n' "$CONFIG"
printf 'install_log=%s\n' "$YWD_TNC_LOG_FILE"
if [[ "$packetlog_state" == enabled ]]; then
  printf 'packet_logs=/var/log/ywd-packetlog/YYYY-MM-DD.{log,jsonl}\n'
fi
printf '\nUseful commands:\n'
printf '  sudo systemctl status %s\n' "$SERVICE"
printf '  sudo journalctl -u %s -f\n' "$SERVICE"
printf '  sudo systemctl status %s\n' "$PACKETLOG_SERVICE"
printf '  sudo journalctl -u %s -f\n' "$PACKETLOG_SERVICE"
printf '  sudo /opt/ywd-mmdvm-tnc/source/firmware/probe.sh\n'
