# YWD-MMDVM-TNC

**YWD-MMDVM-TNC** turns a supported MMDVM_HS Raspberry Pi HAT into a dedicated
1200-baud Bell-202 / AX.25 TNC with **TCP KISS** as the primary application
interface. It is designed to sit underneath packet applications such as
LinBPQ/BPQ32, APRS software, terminals, and other KISS-capable tools.

```text
LinBPQ / BPQ32 / packet applications
                |
          TCP KISS :8001
                |
          YWD-MMDVM-TNC
                |
       AX.25 + Bell-202 + CSMA
                |
     qualified MMDVM_HS firmware
                |
          STM32 + ADF7021
                |
              RF
```

The TNC intentionally stops at the modem boundary. Connected-mode AX.25 state,
acknowledgements, retries, routing, BBS/node features, beacons, and application
logic belong to the program using KISS. One `ywd-tncd` process owns the HAT UART
and provides the shared RX/TX modem path.

## Qualified hardware support

The physically-qualified target is:

- Raspberry Pi 5
- MMDVM_HS-style simplex HAT
- STM32F103 / ADF7021
- 14.7456 MHz TCXO target profile
- modem UART `/dev/ttyAMA0`

The current product TX safety gate permits RF transmit only on the physically
qualified **145.050 MHz / power-200** profile. RX may be configured separately,
but users are responsible for choosing frequencies and operating parameters
that are legal and appropriate for their station and location.

## Quick install

On a fresh Raspberry Pi OS / Debian-family system, start the guided installer
with one command:

```bash
curl -fsSL https://raw.githubusercontent.com/merberg-ai/ywd-mmdvm-tnc/main/install.sh | sudo bash
```

The installer keeps detailed command output in `/var/log/ywd-mmdvm-tnc/` and
shows only the current step, progress, warnings, prompts, and final result in the
terminal.

The guided path performs the complete installation workflow:

1. installs the required host and STM32 toolchain packages;
2. initializes and verifies the exact pinned modem runtime/flash-support core
   and verifies the in-repo qualified firmware engineering provenance;
3. installs YWD-MMDVM-TNC into `/opt/ywd-mmdvm-tnc`;
4. creates `/etc/ywd-mmdvm-tnc/config.toml` from the operator's answers;
5. verifies an existing qualified firmware artifact or reproducibly builds it
   twice from the in-repo firmware inputs as a non-root user and checks the
   exact expected SHA-256;
6. identifies the HAT and, when starting from recognized stock firmware,
   captures and verifies two independent full-flash rollback reads;
7. requires the explicit `WRITE-FIRMWARE-NOW` confirmation immediately before
   any stock-to-product firmware write;
8. reads the programmed bytes back independently and verifies the running
   qualified firmware identity;
9. enables and starts `ywd-mmdvm-tnc.service` and prints the KISS endpoint.

During setup it asks for:

- **KISS access** — local only (`127.0.0.1`) or trusted LAN (`0.0.0.0`)
- **KISS TCP port** — default `8001`
- **RF transmit** — default **disabled**
- **receive frequency** — default `145.050 MHz` when TX is disabled
- optional local **AGW raw** listener — default disabled
- whether to back up / verify / install the qualified HAT firmware — default yes

A real firmware write is never silent. Before writing STM32 main flash the tool
first verifies a protected stock rollback backup and then requires the operator
to type:

```text
WRITE-FIRMWARE-NOW
```

After successful setup the installer enables and starts
`ywd-mmdvm-tnc.service` and prints the configured KISS endpoint.

## Manual installation from Git

```bash
git clone --recursive https://github.com/merberg-ai/ywd-mmdvm-tnc.git
cd ywd-mmdvm-tnc
sudo ./installer/setup.sh
```

The recursive clone is still required because the modem runtime and qualified
HAT probe/flash support remain pinned to the qualified YWD-1278 core. Firmware
**builds**, however, no longer execute the YWD-1278 firmware builder; their
engineering inputs and deterministic builder are carried directly in this repo.

`installer/setup.sh` is the same guided workflow used by the one-line bootstrap.
The lower-level scripts remain available for manual maintenance:

```text
installer/bootstrap.sh   install OS/toolchain dependencies
installer/install.sh     install/update product files and systemd service
firmware/ensure.sh       verify or build the exact qualified firmware artifact
firmware/build.sh        reproducibly build the exact qualified firmware
firmware/probe.sh        verify the running HAT firmware without configuring RF
firmware/flash.sh        probe, back up, verify, or flash through the qualified path
```

## Installed layout

```text
/opt/ywd-mmdvm-tnc/source
/opt/ywd-mmdvm-tnc/venv
/etc/ywd-mmdvm-tnc/config.toml
/var/lib/ywd-mmdvm-tnc
/var/log/ywd-mmdvm-tnc
/etc/systemd/system/ywd-mmdvm-tnc.service
```

Useful commands:

```bash
sudo systemctl status ywd-mmdvm-tnc.service
sudo systemctl restart ywd-mmdvm-tnc.service
sudo journalctl -u ywd-mmdvm-tnc.service -f
```

## Configuration

A safe local-only configuration looks like:

```toml
[hardware]
target = "mmdvm-hs-hat-stm32f103-simplex-14.7456-adf7021"

[radio]
device = "/dev/ttyAMA0"
frequency_mhz = 145.050
tx_power = 200
tx_enabled = false

[packet]
baud = 1200
txdelay_ms = 300
persist = 63
slottime_ms = 100

[kiss]
enabled = true
listen = "127.0.0.1"
port = 8001
allow_wildcard_bind = false

[agw]
enabled = false
listen = "127.0.0.1"
port = 8000
allow_wildcard_bind = false
raw_only = true
```

The installed file also contains the exact qualified firmware identity and the
hard-disabled automatic-flash setting used by the runtime safety checks. The
installer writes only YWD-MMDVM-TNC hardware, radio, packet, listener, and
firmware settings; it does not write LinBPQ/BPQ, node, BBS, routing, or other
application configuration.

### LAN KISS access

For a packet application running on another trusted LAN machine:

```toml
[kiss]
enabled = true
listen = "0.0.0.0"
port = 8001
allow_wildcard_bind = true
```

`0.0.0.0` is only the **bind address on the TNC Raspberry Pi**. Remote clients
connect to the Pi's real LAN address, for example `192.168.1.50:8001`.

KISS has no authentication or encryption. Do not expose TCP/8001 to the public
Internet, an untrusted wireless network, or an unfiltered tunnel/VPN interface.
Use a host/network firewall when needed.

## LinBPQ example

A typical LinBPQ port on another LAN host is:

```text
PORT
 ID=YWD-MMDVM-TNC
 TYPE=ASYNC
 PROTOCOL=KISS
 IPADDR=192.168.1.50
 TCPPORT=8001
 KISSOPTIONS=NOPARAMS
 FRACK=7000
 RESPTIME=1000
 RETRIES=10
 MAXFRAME=2
 PACLEN=128
 TXDELAY=300
 SLOTTIME=100
 PERSIST=63
 FULLDUP=0
ENDPORT
```

Replace `192.168.1.50` with the actual address of the YWD-MMDVM-TNC Pi.
`KISSOPTIONS=NOPARAMS` leaves the TNC's configured TXDELAY/PERSIST/SLOTTIME
values authoritative.

The project has been physically tested with remote LinBPQ over LAN TCP KISS in
both directions, including sustained connected-mode traffic and multi-frame
transfers. Qualification records are stored under `qualification/`.

## Firmware updates and explicit reflashing

The normal installer is intentionally conservative: if the exact accepted firmware is already running, it verifies programmed bytes instead of rewriting flash. For an explicit firmware update or a deliberate reflash of the active accepted image, use:

```bash
sudo ywd-update-firmware
```

The updater does **not** accept an arbitrary `.bin`. It loads the repository-owned `firmware/accepted-firmware.json` registry, verifies the exact active profile and artifact, requires a verified stock rollback backup, and still requires the interactive `WRITE-FIRMWARE-NOW` confirmation before main flash is written. Programmed bytes are read back and verified before the HAT is restarted. This is also the intended path for future qualified firmware revisions.

## Firmware

The RF-critical modem firmware remains the exact physically-qualified AX25R4
image. Its source lineage is preserved as pinned provenance, while the build
inputs needed to reproduce it are now stored directly in YWD-MMDVM-TNC:

```text
firmware/build-qualified-inrepo.py
firmware/tooling/packet-rssi-build-manifest.json
firmware/tooling/qualified-toolchain.json
firmware/vendor/ywd-mmdvm/
```

The historical qualified YWD-1278 core remains pinned at:

```text
c28c46c3478d7931af611923c92cd8f692a00858
```

That submodule is still used by the modem runtime and qualified HAT
probe/flash-support path. It is **not** used by `firmware/build.sh` to construct
the firmware image.

Qualified firmware artifact:

- canonical repo path: `firmware/out/0c-p2-rssi-ax25r4-stm32f103-simplex-adf7021-14.7456tcxo-8mhz-hse/`
- size: `59,892` bytes
- SHA-256: `b06fcbf0baa36e865198091cee27c66e1624ef08117ee685253a7a5613c7c616`
- flash base: `0x08000000`
- STM32 bootloader version: `0x22`
- STM32 device ID: `0x0410`

The firmware binary intentionally retains its historical, physically-qualified
runtime identity:

```text
MMDVM_HS_Hat-YWD-1278-AX25R4-v0.1.0-alpha1 14.7456MHz ADF7021 FW based on CA6JAU GitID #7ff74ed
```

That string is not cosmetic: changing it would produce a different firmware
binary and discard the exact byte-level qualification evidence. All product
paths, services, commands, installer UX, state, and documentation use the
**YWD-MMDVM-TNC** name while the immutable firmware identity remains preserved
for provenance.

### Reproducible toolchain

The byte-identical build environment is recorded in
`firmware/tooling/qualified-toolchain.json`. The qualified ARM compiler is GCC
`14.2.1`; CI additionally pins the Debian Trixie container and the exact
compiler, binutils, newlib, and ARM libstdc++ package versions that reproduced
the physically-qualified image.

The production build wrapper verifies the exact qualified builder blob and GCC
version before it builds, then still requires the final artifact to match the
qualified size and SHA-256. A compiler/environment drift therefore fails closed
rather than silently producing a new firmware image.

### Build the exact firmware

Firmware builds are intentionally non-root:

```bash
./firmware/build.sh
```

The production wrapper runs `firmware/build-qualified-inrepo.py`, which
materializes the byte-pinned engineering transforms from `firmware/vendor/`,
fetches only the pinned upstream MMDVM_HS source/submodule revisions recorded in
the manifest, performs two independent builds, and requires them to be
byte-identical before the product profile verifies the exact expected SHA-256.
No YWD-1278 firmware builder is executed.

When the guided installer itself is running as root, `firmware/build.sh` exports
only the qualified in-repo firmware builder/tooling/engineering inputs into an
isolated temporary workspace, hands that workspace to the original non-root
sudo user, performs the two builds there, and copies only the verified artifact
(and build metadata when present) back into the installer workspace.

Build details are written to the displayed log file. No HAT, GPIO, flash, or RF
access occurs. The guided installer performs this automatically when the
artifact is absent.

### Probe the installed firmware

Stop the running modem service first so the UART is available:

```bash
sudo systemctl stop ywd-mmdvm-tnc.service
sudo ./firmware/probe.sh
```

The probe performs only the safe firmware identity transaction and does not
configure RF or write flash.

### Back up stock firmware

```bash
sudo ./firmware/flash.sh backup
```

A valid stock backup uses two independent full main-flash reads. They must be
byte-identical and must match the physically-qualified stock SHA-256 before the
backup is accepted.

### Install / verify the qualified firmware manually

If the qualified artifact has not been built yet:

```bash
./firmware/build.sh
```

Then run the qualified deployment path:

```bash
sudo ./firmware/flash.sh flash --authorize FLASH-QUALIFIED-AX25R4
```

If the exact product firmware is already installed, the tool verifies the
programmed bytes without rewriting main flash. If stock firmware is running,
the tool first creates/verifies the protected rollback backup and then requests
the explicit `WRITE-FIRMWARE-NOW` confirmation immediately before the write.
Programmed bytes are read back independently afterward. Option bytes are never
written.

## Safety model

- RF TX defaults disabled.
- Product TX is currently restricted to the physically-qualified
  145.050 MHz / power-200 profile.
- Installation does not silently enable TX.
- Firmware writes are never automatic or silent.
- A verified stock rollback image is required before a stock-to-product write.
- Programmed firmware is independently read back and verified.
- STM32 option bytes are never written.
- One process owns the HAT UART.
- KISS and AGW clients receive live frames only; historical RF packets are not
  replayed to new clients.
- Connected-mode retries/session state remain in the external packet stack,
  not in the modem service.

## Qualification

The current stack has passed physical tests for:

- live Bell-202 / AX.25 receive on the qualified HAT
- one-shot TCP KISS transmit with independent over-air decode
- RX recovery on the same KISS connection after TX
- no automatic modem-layer TX retry
- remote LAN TCP KISS operation
- real LinBPQ bidirectional interoperability and sustained connected-mode traffic
- the complete fresh-stock-HAT public installation path: one-command bootstrap,
  guided configuration, reproducible non-root firmware build, exact artifact
  verification, protected two-pass stock backup, explicit flash confirmation,
  stock-to-qualified firmware deployment, service startup, and working LinBPQ
  TX/RX afterward

Machine-readable evidence is kept in `qualification/` and exact historical
checkpoint branches are retained in Git. The public stock-HAT installer record
is `qualification/public-stock-hat-install-physical-2026-09-07.json`.

## License

Host-side YWD code is GPL-2.0-or-later. Firmware derived from MMDVM_HS retains
its applicable upstream GPL notices and attribution. See `LICENSING.md` and the
pinned vendor source for provenance.
