# YWD Firmware Studio

**Status:** FM0 development on dev-fws. Host-only; no hardware support yet.

Firmware Studio is a separate, on-demand web application. It is not the
qualified, always-on, read-only packet monitor WebUI, and cannot transmit,
flash, query the modem UART, or control GPIO in FM0.

## Architectural boundaries

- Packet monitor: ywd-webui, normally 127.0.0.1:8088; remains passive.
- Firmware Studio: ywd-fws, default 127.0.0.1:8090; starts only on demand
  in an unprivileged user shell, never enabled at boot.
- FM0 is a login-protected web shell with disabled operation tiles. It has no
  privileged firmware agent, API bridge, shell endpoint, subprocess call,
  HAT probe, automatic flash, or RF operation.
- A future privileged non-network broker will expose narrow allowlisted
  actions, independent authorization, exclusive locks, verified backups,
  hardware checks, deliberate approvals, progress and readback.
- Login alone will never confer permission to flash. No browser field may
  ever become a command passed to a shell interpreter.
- FM0 supports only IPv4 loopback listeners. To connect from a different
  computer or phone, use SSH port forwarding. Do not expose 8090 over an
  unencrypted LAN listener or public router port.

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

On a desktop, create an SSH tunnel to the Pi:

~~~sh
ssh -L 8090:127.0.0.1:8090 ywd@192.168.1.11
~~~

Then browse to http://127.0.0.1:8090/ on the desktop. Substitute the
actual address of the Pi if different.

After an explicit Python package install, the entry point is ywd-fws.
The product installer does not yet provision or start Firmware Studio.

## Security in FM0

- Owner-only mode-0600 credentials and salted scrypt hash; no plaintext
  password in the TOML file.
- Random server-side sessions with an expiration deadline; cookies use
  HttpOnly and SameSite=Strict. Logout requires session CSRF and same Origin.
- IP-based login rate limiting, and no-store, framing and CSP restrictions.
- Host checks help prevent DNS rebinding; non-loopback binds fail closed.
- The console shows local UI events only. It cannot execute commands or
  create synthetic HAT or flash progress records.
- HTTPS and direct LAN access are not provided in FM0. A future encrypted
  remote-access design must be reviewed independently.

## Roadmap

- FM0: on-demand app, login, isolated shell, host tests and documentation.
- FM1: target inventory and protected dual-read backup through a narrowly
  controlled privileged non-network broker; no arbitrary shell endpoints.
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
