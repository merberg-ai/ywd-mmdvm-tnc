# YWD Firmware Studio

**Status:** FM1 development on dev-fws. Inventory is browser-accessible; hardware backup requires explicit root terminal authorization. No browser firmware operations.

Firmware Studio is a separate, on-demand web application. It is not the
qualified, always-on, read-only packet monitor WebUI, and cannot transmit,
flash, query the modem UART, or control GPIO in FM0.

## Architectural boundaries

- Packet monitor: ywd-webui, normally 127.0.0.1:8088; remains passive.
- Firmware Studio: ywd-fws, default 127.0.0.1:8090; starts only on demand
  in an unprivileged user shell, never enabled at boot.
- FM1 adds authenticated GET /api/inventory. The unprivileged web server
  reads the pinned firmware profile and lists backups whose storage is
  accessible. It never opens or queries the modem UART.
- The optional root-owned *read-only* inventory broker has one Unix-socket
  opcode, INVENTORY, and checks the caller's Unix UID with SO_PEERCRED.
  It is a separate manual process, only for verifying/listing protected
  backup records. It never stops services or touches the HAT.
- A separate, **operator-invoked root/TTY-only** backup worker can perform
  two 128 KiB readbacks using the existing qualified bootloader/GPIO
  control. It is never called through HTTP or the inventory broker.
- No firmware flash/restore endpoint, general subprocess API, privileged
  browser-to-worker action, or RF operation is implemented.
- A future privileged non-network broker will expose narrow allowlisted
  actions, independent authorization, exclusive locks, verified backups,
  hardware checks, deliberate approvals, progress and readback.
- Login alone will never confer permission to flash. No browser field may
  ever become a command passed to a shell interpreter.
- FM0 listens only on loopback by default. An **explicitly opted-in**
  *specific RFC1918 private LAN IPv4 address* is allowed for development over
  PiVPN/WireGuard. Wildcard (0.0.0.0), public, carrier-grade NAT, link-local
  and IPv6 binds remain disallowed. Never forward port 8090 on your router.

## Try FM0 without touching the running TNC

From an ordinary, non-root shell in your existing checkout:

~~~sh
cd ~/ywd-mmdvm-tnc
git fetch origin
git switch dev-fws
git pull --ff-only origin dev-fws

# Host-only qualification gate
bash scripts/check-fws.sh

# Create password credentials; twice, minimum 12 characters
PYTHONPATH=src python3 -m ywdfws.app \
  --config config/ywd-fws.example.toml --init-password

# Start temporary web application in foreground
PYTHONPATH=src python3 -m ywdfws.app \
  --config config/ywd-fws.example.toml
~~~

Open http://127.0.0.1:8090/ locally, sign in, and examine the shell.
Stop with Ctrl+C. No deployed services are changed or restarted.

### Temporary phone access over PiVPN/WireGuard

When your phone is on the WireGuard VPN, stop the existing FM0 server with
Ctrl+C and restart it as your normal (non-root) Pi user:

~~~sh
cd ~/ywd-mmdvm-tnc
git pull --ff-only origin dev-fws
bash scripts/check-fws.sh
PYTHONPATH=src python3 -m ywdfws.app \
  --config config/ywd-fws.example.toml \
  --listen 192.168.1.11 --allow-private-bind
~~~

Open http://192.168.1.11:8090/ on the phone while WireGuard is connected.
If the Pi now has a different LAN address, substitute its real address.
This opt-in applies only to the running process and does not alter the safe
default configuration. It cannot be accessed via a public-facing listener.

For a persistent, **explicit** test profile, edit a local config file to set:

~~~toml
[web]
listen = "192.168.1.11"
port = 8090
allow_private_bind = true
~~~

Use that file with --config; the existing [auth] settings should also be
retained.

**Transport warning:** the HTTP password and session cookie are unencrypted
on any LAN hop *after* WireGuard terminates at the PiVPN gateway. Use only
a trusted LAN, restrict TCP/8090 to your trusted VPN client paths where
possible, and do not use the private-HTTP option for future firmware-write
functions. Those should require HTTPS or equivalent end-to-end encryption.

### Alternatively: SSH tunnel on a desktop

On a desktop, create an SSH tunnel to the Pi:

~~~sh
ssh -L 8090:127.0.0.1:8090 ywd@192.168.1.11
~~~

Then browse to http://127.0.0.1:8090/ on the desktop. Substitute the
actual address of the Pi if different.

After an explicit Python package install, the entry point is ywd-fws.
The product installer does not yet provision or start Firmware Studio.

## FM1: Inventory and backup architecture

After starting Firmware Studio and signing in, the overview loads the
**reference target profile** and any accessible backup records. It does
NOT claim to live-detect the installed firmware. The modem can keep
running, and the existing monitor on port 8088 is unchanged.

### Optional root-only *read-only* backup catalog

The existing stock rollback backups are normally stored with root-only
permissions under /var/lib/ywd-mmdvm-tnc/firmware-backups, so the
unprivileged server correctly reports "restricted" if it cannot read them.

To display verified backups on the phone without weakening those folder
permissions, open a **second SSH session** to the Pi as the ywd account,
in the dev-fws checkout, and launch:

~~~sh
cd ~/ywd-mmdvm-tnc
sudo env PYTHONPATH="$PWD/src" python3 -m ywdfws.broker --web-user ywd
~~~

This is an on-demand root process with one strict metadata-only request.
It creates a socket at /run/ywd-fws-inventory.sock, gives access only to
the ywd Unix UID, and removes its socket at shutdown. It does not start
automatically. Leave it running for the catalog test, then stop with
Ctrl+C. In the WebUI, tap **Refresh**. You should now see existing backup
manifests, SHA-256 validated against their image data, and trust labels.

- **GOLDEN STOCK / VERIFIED**: matches the physically-qualified golden
  stock SHA-256, target and full image size. A future restore tool may
  accept it after additional safety checks.
- **CAPTURE / VERIFIED**: two reads match with a validated captured image,
  but that image is NOT approved for restoration by this milestone.
- **INVALID / UNKNOWN**: metadata or image verification failed.
- Invalid/unknown records are shown for diagnosis but never offered as
  restoration candidates.

### Creating a new backup: CLI-only maintenance worker

FM1 provides an opt-in read-only-flash worker at
firmware/fws_backup.py. Running it **temporarily stops the active modem
UART-owning service, enters the STM32 ROM bootloader, reads all 131072
bytes twice, validates both reads, restarts the HAT application, and
attempts to restore previously active modem services**.

This is a real hardware maintenance operation. **Do not perform it merely
to test the new WebUI.** It must be operator-approved for a maintenance
window, and interrupted jobs may require manual service recovery.

For a future explicitly approved maintenance window:

~~~sh
cd ~/ywd-mmdvm-tnc
sudo python3 firmware/fws_backup.py backup
~~~

The worker requires a real TTY and an exact typed confirmation,
READ-HAT-MAIN-FLASH. It hardcodes the qualified /dev/ttyAMA0 and verified
target profile, rejects other running identities, and can never write
main flash or option bytes. It locks out overlapping FM1 backup jobs.
It does **not** automatically execute from the browser.

- Exact pinned *stock* firmware is handled by the existing physically
  qualified golden-backup workflow with a strict SHA-256 comparison.
- Exact pinned *YWD AX25R4* firmware is captured using two independent
  full-flash reads, hashed and stored as an fws-backup-1 image. Its
  restore_qualified flag is always false.
- Unknown or non-allowlisted firmware is rejected without backup.

Root-private backup files remain in the existing backup root, with
0600 file permissions and 0700 directory permissions. No raw binary is
served from the WebUI, no arbitrary backup paths are accepted from a
browser, and no firmware-writing operations exist in this milestone.

## Security in FM0

- Owner-only mode-0600 credentials and salted scrypt hash; no plaintext
  password in the TOML file.
- Random server-side sessions with an expiration deadline; cookies use
  HttpOnly and SameSite=Strict. Logout requires session CSRF and same Origin.
- IP-based login rate limiting, and no-store, framing and CSP restrictions.
- Host checks help prevent DNS rebinding; any LAN listener requires an explicit
  RFC1918 IP and opt-in. Wildcard and public binds fail closed.
- The console shows local UI events only. It cannot execute commands or
  create synthetic HAT or flash progress records.
- FM0 supports an explicitly opted-in private HTTP bind for VPN-connected
  development *only*. Future authenticated firmware-write operations must
  require end-to-end protected transport, not merely a password over HTTP.

## Roadmap

- FM0: on-demand app, login, isolated shell, host tests and documentation. Completed.
- FM1: reference inventory, verified backup catalog via read-only Unix broker,
  and separate operator-authorized dual-read stock/YWD backup CLI. The WebUI
  cannot trigger UART/backup, and future secure action transport remains TODO.
- FM2: qualified YWD flashing and verified stock backup restore; physical
  target checks, recovery of unbootable firmware, readback verification,
  approval and safe daemon lifecycle.
- FM3: pinned official MMDVM_HS clone/build with STM32 HSE 8 MHz,
  ADF7021 TCXO 14.7456 MHz, explicit artifact inspection and optional
  individually authorized flash.
- FM4: durable console logs, measured progress and operation history,
  explicit close/shutdown, recovery-state handling and UI polish.

Host gates precede any operator-approved physical Pi/HAT qualification.
The qualified AX25R4 firmware and modem path stay frozen for FM0.

## Stock firmware provenance

The pinned upstream source is juribeparada/MMDVM_HS at commit
7ff74ed1ba663a282edcbbb5e0ec3d7132e6f2f5. The main flash capacity
is 131072 bytes, and a specific original stock backup hash is pinned in
firmware/product-ax25r4.json. This is NOT permission to flash an arbitrary
binary, and a new stock build may differ from the original backup.
