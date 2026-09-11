<div align="center">

# YWD-MMDVM-TNC

**A dedicated 1200-baud Bell-202 / AX.25 TNC for supported MMDVM_HS Raspberry Pi HATs**

TCP KISS · optional AGW raw access · passive RX/TX event stream · packet logging · reproducible qualified firmware

[![CI](https://github.com/merberg-ai/ywd-mmdvm-tnc/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/merberg-ai/ywd-mmdvm-tnc/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.11%2B-blue)
![License](https://img.shields.io/badge/License-GPL--2.0--or--later-blue)
![Status](https://img.shields.io/badge/status-alpha-orange)

</div>

YWD-MMDVM-TNC turns a supported **MMDVM_HS simplex HAT** into a standalone
1200-baud packet modem/TNC for Raspberry Pi. The Raspberry Pi owns the HAT,
Bell-202 modem path, channel access, and RF lifecycle; packet applications talk
to it over **TCP KISS** or the optional **AGW raw-frame network interface**.
A separate passive monitor stream can expose decoded RX frames and the complete
TX lifecycle to loggers, dashboards, and telemetry consumers without giving
those observers any transmit authority.

It is intentionally a **modem boundary**, not another node or BBS. LinBPQ,
BPQ32, XRouter, APRS software, terminals, and other packet applications remain
responsible for higher-level AX.25 behavior such as connected-mode state,
acknowledgements, retries, routing, digipeating, BBS/node services, and
application logic.

> [!IMPORTANT]
> YWD-MMDVM-TNC is currently an alpha project with a deliberately narrow,
> physically-qualified hardware and RF envelope. Read the hardware and RF
> profile sections before transmitting.

## Table of contents

- [What it does](#what-it-does)
- [Architecture](#architecture)
- [Qualified hardware](#qualified-hardware)
- [Quick install](#quick-install)
- [After installation](#after-installation)
- [RF operating profiles](#rf-operating-profiles)
- [Connect packet software](#connect-packet-software)
  - [TCP KISS](#tcp-kiss)
  - [LinBPQ example](#linbpq-example)
  - [AGW raw mode](#agw-raw-mode)
  - [Passive monitor event stream](#passive-monitor-event-stream)
- [Packet logging](#packet-logging)
- [Configuration](#configuration)
- [Firmware management](#firmware-management)
- [How the firmware and host software fit together](#how-the-firmware-and-host-software-fit-together)
- [Troubleshooting](#troubleshooting)
- [Safety model](#safety-model)
- [Qualification](#qualification)
- [Repository layout](#repository-layout)
- [License](#license)

## What it does

YWD-MMDVM-TNC provides a small, single-purpose packet-radio stack around the
MMDVM_HS hardware:

- **1200-baud Bell-202 AFSK / AX.25** receive and transmit through the qualified HAT firmware;
- **TCP KISS** for local or trusted-LAN packet applications;
- an optional **AGW raw-frame network interface**;
- a live, read-only **NDJSON monitor stream** for RX frames and TX lifecycle events;
- an optional `ywd-packetlog` service that writes human-readable and structured daily logs;
- half-duplex channel access and TX delay handling;
- one process with exclusive ownership of the HAT UART;
- fail-closed RF transmit profiles;
- safe firmware identity checks, stock-firmware backup, explicit flashing, and readback verification;
- a guided installer that can reproducibly build the exact qualified firmware image when needed.

YWD-MMDVM-TNC does **not** implement a packet node, BBS, mailbox, AX.25
connected-mode session manager, routing layer, or APRS application. Those jobs
belong to the software using the TNC.

## Architecture

```mermaid
flowchart TD
    APP[LinBPQ / XRouter / BPQ32 / APRS / packet application]
    KISS[TCP KISS :8001]
    AGW[Optional AGW raw :8000]
    TNCD[ywd-tncd]
    MON[Passive monitor NDJSON :8002]
    LOGGER[ywd-packetlog / dashboard / telemetry]
    ACCESS[AX.25 framing / Bell-202 / half-duplex channel access]
    FW[Qualified MMDVM_HS AX25R4 firmware]
    HW[STM32F103 + ADF7021]
    RF[RF]

    APP --> KISS
    APP -. optional .-> AGW
    KISS --> TNCD
    AGW --> TNCD
    TNCD --> ACCESS
    ACCESS --> FW
    FW --> HW
    HW --> RF
    TNCD -. read-only events .-> MON
    MON --> LOGGER
```

At runtime, one `ywd-tncd` process owns the modem UART and creates the shared
RX/TX backend used by KISS and, when enabled, AGW raw mode. Incoming RF frames
are delivered live to connected clients; YWD-MMDVM-TNC does not replay
historical RF traffic to newly connected clients.

The monitor path is deliberately side-band. It receives copies of live RX and
TX lifecycle events but is not part of admission, CSMA, modem ownership, or RF
transmit. Each observer gets a bounded queue; if an observer is too slow,
monitor events are dropped for that observer rather than blocking packet RX/TX.
The monitor protocol contains no command or transmit API.

For connected-mode AX.25, the division of responsibility looks like this:

| YWD-MMDVM-TNC | Packet application |
| --- | --- |
| HAT UART ownership | AX.25 connection state |
| Bell-202 RX/TX | SABM/UA/DISC/session policy |
| KISS / AGW raw transport | acknowledgements and retries |
| bounded TX admission | routing / digipeating |
| TX delay and half-duplex RF lifecycle | node / BBS / APRS logic |
| RF profile enforcement | user-facing application behavior |
| passive lifecycle observation | application/session policy |

## Qualified hardware

The physically-qualified product target is:

| Component | Qualified target |
| --- | --- |
| Host | Raspberry Pi 5 |
| HAT | MMDVM_HS-style **simplex** HAT |
| MCU | STM32F103 |
| RF IC | ADF7021 |
| TCXO profile | 14.7456 MHz |
| Modem UART | `/dev/ttyAMA0` |
| Packet mode | 1200-baud Bell-202 / AX.25 |

Other Raspberry Pi models or MMDVM_HS board variants may share enough hardware
to work, but they are **not represented by the current physical qualification
evidence**. The installer and runtime intentionally reject unsupported product
targets rather than silently guessing.

## Quick install

On Raspberry Pi OS or another supported Debian-family system, run:

```bash
curl -fsSL https://raw.githubusercontent.com/merberg-ai/ywd-mmdvm-tnc/main/install.sh | sudo bash
```

The installer is interactive. It keeps detailed command output under
`/var/log/ywd-mmdvm-tnc/` while the terminal shows the current step, prompts,
warnings, and final result.

### What the installer asks

During setup you choose:

- **KISS access** — local-only (`127.0.0.1`) or trusted-LAN (`0.0.0.0`);
- **KISS TCP port** — default `8001`;
- **RF transmit** — default **disabled**;
- **receive frequency** — default `145.050 MHz` when TX is disabled;
- optional **AGW raw** listener — default disabled on `127.0.0.1:8000`;
- the local passive monitor stream — default endpoint `127.0.0.1:8002` on new configurations;
- optional persistent packet logging through `ywd-packetlog.service`;
- whether to back up, verify, and install the qualified HAT firmware — default yes.

Existing configurations created before monitor-events remain valid. If
`[monitor]` is absent, runtime compatibility defaults it to disabled. Guided
setup can add the loopback-only monitor block while preserving the existing
radio, KISS, AGW, and packet settings, and makes a configuration backup first.
If an older packet logger is already enabled/running, setup can migrate it to
the first-class monitor stream.

If TX is enabled during the initial guided setup, the installer offers the
historically physically-qualified **145.050 MHz / power-200 packet profile**.
The APRS profile can be selected later with `ywd-tnc-profile`.

### What the installer does

The complete guided path:

1. installs the required host and STM32 build-tool dependencies;
2. verifies the pinned qualification/provenance inputs and in-repo product runtime;
3. installs YWD-MMDVM-TNC under `/opt/ywd-mmdvm-tnc`;
4. creates or preserves `/etc/ywd-mmdvm-tnc/config.toml`;
5. installs the TNC service and passive packet-logger service definition;
6. verifies an existing qualified firmware artifact or reproducibly builds it twice as a non-root user;
7. requires the resulting firmware to match the exact accepted size and SHA-256;
8. identifies the HAT and, when starting from recognized stock firmware, creates two independent full-flash rollback reads and verifies they are identical;
9. requires the operator to type `WRITE-FIRMWARE-NOW` immediately before any stock-to-product firmware write;
10. independently reads the programmed bytes back and verifies the running firmware identity;
11. enables and starts `ywd-mmdvm-tnc.service`, optionally enables packet logging when explicitly requested, and prints the configured endpoints.

A firmware write is never hidden inside installation. If a real write is needed,
you will see an explicit warning and must type:

```text
WRITE-FIRMWARE-NOW
```

### Manual installation from Git

```bash
git clone --recursive https://github.com/merberg-ai/ywd-mmdvm-tnc.git
cd ywd-mmdvm-tnc
sudo ./installer/setup.sh
```

The recursive clone is currently required because the installer still verifies
the exact pinned YWD-1278 qualification/provenance anchor. The active product
runtime and HAT support used by YWD-MMDVM-TNC have been brought into this
repository; normal host runtime operation and the production firmware builder
do not import or execute the historical YWD-1278 implementation from the
submodule.

Useful lower-level maintenance scripts are retained for operators and developers:

```text
installer/bootstrap.sh       install OS/toolchain dependencies
installer/install.sh         install/update product files and systemd services
firmware/ensure.sh           verify or build the exact accepted firmware artifact
firmware/build.sh            reproducibly build the exact accepted firmware
firmware/probe.sh            verify the running HAT firmware identity
firmware/flash.sh            probe, back up, verify, or flash through the qualified path
scripts/check-monitor-events.sh
                             host-only monitor/logger contract
scripts/monitor-events-physical.sh
                             observation-only real Pi/HAT monitor qualification gate
```

## After installation

The main service is:

```text
ywd-mmdvm-tnc.service
```

Useful commands:

```bash
sudo systemctl status ywd-mmdvm-tnc.service
sudo systemctl restart ywd-mmdvm-tnc.service
sudo journalctl -u ywd-mmdvm-tnc.service -f
```

Check the active RF profile with:

```bash
ywd-tnc-profile status
```

The normal local endpoints are:

```text
TCP KISS       127.0.0.1:8001
AGW raw        127.0.0.1:8000   (optional / disabled by default)
Monitor NDJSON 127.0.0.1:8002   (passive observer stream)
```

If you selected trusted-LAN KISS access during installation, the KISS service
binds to `0.0.0.0:8001`; clients connect to the Raspberry Pi's **actual LAN IP
address**, not to `0.0.0.0`. The monitor should normally remain loopback-only.

Installed files live primarily at:

```text
/opt/ywd-mmdvm-tnc/source
/opt/ywd-mmdvm-tnc/venv
/etc/ywd-mmdvm-tnc/config.toml
/var/lib/ywd-mmdvm-tnc
/var/log/ywd-mmdvm-tnc
/var/log/ywd-packetlog
/etc/systemd/system/ywd-mmdvm-tnc.service
/etc/systemd/system/ywd-packetlog.service
```

## RF operating profiles

RF transmit is deliberately bounded. The current product accepts only the named
operating profiles below; arbitrary TX frequency/power combinations are rejected.

| Profile | Frequency | TX power | TX state | Status |
| --- | ---: | ---: | --- | --- |
| `packet` | 145.050 MHz | 200/255 | enabled | **physically qualified historical packet profile** |
| `aprs` | 144.390 MHz | 200/255 | enabled | permitted APRS operational profile; **not separately physically qualified** |
| `rx-only` | current frequency | current power value | disabled | receive-only safety mode |

Switch profiles with:

```bash
sudo ywd-tnc-profile packet
sudo ywd-tnc-profile aprs
sudo ywd-tnc-profile rx-only

ywd-tnc-profile status
```

A profile change is transactional. The tool:

1. reads `/etc/ywd-mmdvm-tnc/config.toml`;
2. changes only `radio.frequency_mhz`, `radio.tx_power`, and `radio.tx_enabled`;
3. validates the complete candidate configuration;
4. saves the previous configuration as `/etc/ywd-mmdvm-tnc/config.toml.bak`;
5. atomically replaces the configuration;
6. restarts `ywd-mmdvm-tnc.service`;
7. restores the previous configuration and attempts to restart it if the new service start fails.

For implementation details, see [`docs/rf-profiles.md`](docs/rf-profiles.md).

> [!CAUTION]
> The `aprs` profile is intentionally permitted by the product but does not carry
> the same historical physical qualification claim as the 145.050 MHz packet
> profile. You are responsible for choosing legal frequencies, power, antenna,
> identification, and operating practices for your station and location.

### Application handoff

Normally, give only **one packet application at a time** authority to originate
frames through a particular YWD-MMDVM-TNC instance. Stop or disconnect the old
KISS/AGW client, switch the RF profile if necessary, and then start the next
application. `ywd-tncd` remains the single owner of the physical HAT throughout.
Passive monitor clients and `ywd-packetlog` do not have transmit authority and
may remain connected while the packet application operates.

## Connect packet software

### TCP KISS

TCP KISS is the primary application interface and defaults to port `8001`.

For an application on the same Raspberry Pi, the safe default is:

```text
127.0.0.1:8001
```

For another machine on a trusted LAN, configure:

```toml
[kiss]
enabled = true
listen = "0.0.0.0"
port = 8001
allow_wildcard_bind = true
```

Then point the remote application at the Raspberry Pi's actual address, for
example:

```text
192.168.1.50:8001
```

`0.0.0.0` is a **bind address**, not a destination address.

> [!WARNING]
> KISS has no authentication or encryption. Do not expose TCP/8001 directly to
> the public Internet, an untrusted wireless network, or an unrestricted tunnel
> or VPN interface. Use appropriate host/network firewall rules.

### LinBPQ example

YWD-MMDVM-TNC has been physically tested with LinBPQ running on another LAN host
using TCP KISS in both directions, including sustained connected-mode traffic
and multi-frame transfers.

A typical LinBPQ port is:

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

Replace `192.168.1.50` with the YWD-MMDVM-TNC Raspberry Pi's actual LAN address.

`KISSOPTIONS=NOPARAMS` is intentional in the qualified example: it prevents
LinBPQ from overriding the TNC's configured TXDELAY/PERSIST/SLOTTIME values.
LinBPQ remains responsible for FRACK, retries, MAXFRAME, PACLEN, AX.25 connection
state, routing, node behavior, and related policy.

More detail and qualification notes are in
[`docs/linbpq-lan-p3.md`](docs/linbpq-lan-p3.md).

### AGW raw mode

YWD-MMDVM-TNC can optionally expose an **AGW network-protocol raw-frame subset**.
The default listener is:

```text
127.0.0.1:8000
```

Enable it with:

```toml
[agw]
enabled = true
listen = "127.0.0.1"
port = 8000
allow_wildcard_bind = false
raw_only = true
```

This is deliberately **not a claim of full AGWPE server compatibility**. The
implementation provides the raw packet interface needed to share the same modem
backend with compatible applications while keeping AX.25 session/application
logic outside YWD-MMDVM-TNC.

### Passive monitor event stream

The first-class monitor endpoint defaults to:

```text
127.0.0.1:8002
```

It is newline-delimited JSON (NDJSON), schema version `1`. Each record is a
single event. The stream is **live-only**: connecting or reconnecting does not
replay historical traffic.

The current event vocabulary is:

```text
rx.frame

tx.submitted
tx.queued
tx.channel_clear
tx.dispatched
tx.complete

tx.rejected
tx.timeout
tx.failed
```

A successful transmit follows this observable sequence:

```text
application DATA
  -> tx.submitted
  -> tx.queued
  -> qualified CSMA reaches READY
  -> tx.channel_clear
  -> modem-facing selector burst accepted
  -> tx.dispatched
  -> RF becomes idle and RX is restored
  -> tx.complete
```

`tx.channel_clear` is emitted at the existing qualified CSMA boundary, not
inferred from application timing. `tx.dispatched` means downstream modem
submission succeeded. `tx.complete` appears only after the existing half-duplex
lifecycle returns with RF idle and RX restored.

Decoded AX.25 records include source, destination, digipeater path, frame class
and type, control/P-F state, N(S)/N(R) when applicable, PID, printable info,
hex payload, and the complete frame bytes when available. TX lifecycle records
also carry a request ID, KISS parameter generation, packet timing values, RSSI
at channel-clear, and selector-burst metadata when available.

Watch the raw stream locally with:

```bash
nc 127.0.0.1 8002
```

Sending data to that socket has no monitor-protocol meaning. The monitor server
has no command parser and no path to the TX queue.

For the full protocol and safety contract, see
[`docs/monitor-events.md`](docs/monitor-events.md).

## Packet logging

The repository ships `ywd-packetlog`, a reconnecting read-only monitor client,
and `ywd-packetlog.service`. The service consumes `127.0.0.1:8002`; it does not
sniff or originate KISS traffic.

Enable persistent logging explicitly with:

```bash
sudo systemctl enable --now ywd-packetlog.service
```

Useful commands:

```bash
sudo systemctl status ywd-packetlog.service
sudo journalctl -u ywd-packetlog.service -f
```

Daily logs are written in two forms:

```text
/var/log/ywd-packetlog/YYYY-MM-DD.log
/var/log/ywd-packetlog/YYYY-MM-DD.jsonl
```

The `.log` file is operator-friendly and formats connected-mode controls such as
`SABM+`, `RR6-`, `I01-`, `DISC+`, and `UA+`, along with PID, info length,
digipeater path, RSSI, selector count, and payload text where useful. The
`.jsonl` file preserves each complete schema-1 event object for later analysis,
dashboards, telemetry, statistics, or tooling.

Example:

```text
2026-09-10 20:14:34.146 TX QUEUED #6 fm NOCALL-15 to RDG via YWDNOD ctl I01- pid=F0 len=4  bye\r
2026-09-10 20:14:36.548 TX CHANNEL-CLEAR #6 fm NOCALL-15 to RDG via YWDNOD ctl I01- pid=F0 len=4  bye\r raw_rssi=106
2026-09-10 20:14:36.563 TX DISPATCHED #6 fm NOCALL-15 to RDG via YWDNOD ctl I01- pid=F0 len=4  bye\r selectors=618
2026-09-10 20:14:37.115 TX COMPLETE #6 fm NOCALL-15 to RDG via YWDNOD ctl I01- pid=F0 len=4  bye\r
2026-09-10 20:14:39.976 RX fm RDG to NOCALL-15 via YWDNOD* ctl DISC+ len=0
```

Packet logging is opt-in on a new installation. Guided setup may automatically
offer migration when it finds an already-enabled older packet logger.

## Configuration

The main configuration file is:

```text
/etc/ywd-mmdvm-tnc/config.toml
```

A safe local-only, receive-only configuration looks like:

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

[monitor]
enabled = true
listen = "127.0.0.1"
port = 8002
allow_wildcard_bind = false

[firmware]
required_identity = "MMDVM_HS_Hat-YWD-1278-AX25R4-v0.1.0-alpha1 14.7456MHz ADF7021 FW based on CA6JAU GitID #7ff74ed"
allow_automatic_flash = false
```

The runtime validates the complete configuration before taking ownership of the
modem. With TX enabled, the configured frequency and power must match an
explicitly permitted product RF profile. Automatic firmware flashing remains
hard-disabled at runtime.

The monitor uses the same listener-address policy as the other product
listeners. The normal default is loopback. A wildcard bind requires explicit
`allow_wildcard_bind = true`, and public IPv4 bind addresses are rejected.

The installer manages only the TNC's hardware, radio, packet timing, listener,
monitor, and firmware settings. It does not generate LinBPQ/BPQ/XRouter node,
BBS, routing, mailbox, APRS, or application configuration.

## Firmware management

The normal installer is intentionally conservative. If the exact accepted
firmware is already running, it verifies it instead of needlessly rewriting
STM32 flash.

For an explicit firmware update or deliberate reflash of the currently accepted
image, use:

```bash
sudo ywd-update-firmware
```

The updater does **not** accept an arbitrary `.bin`. It reads the
repository-owned `firmware/accepted-firmware.json` registry, verifies the active
profile and artifact, requires a verified stock rollback backup where
applicable, and still requires `WRITE-FIRMWARE-NOW` before writing main flash.
Programmed bytes are read back and verified before the HAT is restarted.

### Build the exact accepted firmware

Firmware builds are intentionally non-root:

```bash
./firmware/build.sh
```

The production wrapper runs `firmware/build-qualified-inrepo.py`. It
materializes the byte-pinned engineering transforms from this repository,
fetches only the pinned upstream MMDVM_HS source/submodule revisions recorded in
the manifest, performs two independent builds, requires them to be
byte-identical, and then requires the resulting artifact to match the accepted
size and SHA-256.

The build path does **not** execute the historical YWD-1278 firmware builder.
No HAT, GPIO, flash, or RF access occurs during the build.

### Probe the running HAT firmware

The service owns the UART, so stop it before a manual probe:

```bash
sudo systemctl stop ywd-mmdvm-tnc.service
sudo /opt/ywd-mmdvm-tnc/source/firmware/probe.sh
sudo systemctl start ywd-mmdvm-tnc.service
```

The probe performs the firmware identity transaction only; it does not configure
RF or write flash.

### Back up recognized stock firmware

```bash
sudo ./firmware/flash.sh backup
```

A valid stock backup uses two independent full main-flash reads. They must be
byte-identical and match the qualified stock SHA-256 before the backup is
accepted.

### Manually install or verify the accepted firmware

Build it first if needed:

```bash
./firmware/build.sh
```

Then use the qualified deployment path:

```bash
sudo ./firmware/flash.sh flash --authorize FLASH-QUALIFIED-AX25R4
```

If the exact accepted firmware is already installed, the normal path verifies
programmed bytes without rewriting main flash. If recognized stock firmware is
running, the tool first creates/verifies the protected rollback backup and then
asks for `WRITE-FIRMWARE-NOW` immediately before the write. STM32 option bytes
are never written.

## How the firmware and host software fit together

There are two separate version/provenance domains in this project:

1. **YWD-MMDVM-TNC host software** — the Python service, installer,
   configuration, KISS/AGW/monitor interfaces, packet logger, RF-profile policy,
   and maintenance tooling;
2. **the immutable physically-qualified HAT firmware artifact** — retained byte
   for byte so its existing qualification evidence remains valid.

The accepted RF-critical modem firmware is the exact AX25R4 image with this
historical runtime identity:

```text
MMDVM_HS_Hat-YWD-1278-AX25R4-v0.1.0-alpha1 14.7456MHz ADF7021 FW based on CA6JAU GitID #7ff74ed
```

That older-looking firmware identity is intentional. It is **not** the version
number of the current YWD-MMDVM-TNC host package. Changing the string would
change the binary and invalidate the exact byte-level qualification anchor.

Qualified firmware artifact details:

| Property | Value |
| --- | --- |
| Canonical path | `firmware/out/0c-p2-rssi-ax25r4-stm32f103-simplex-adf7021-14.7456tcxo-8mhz-hse/` |
| Size | `59,892` bytes |
| SHA-256 | `b06fcbf0baa36e865198091cee27c66e1624ef08117ee685253a7a5613c7c616` |
| Flash base | `0x08000000` |
| STM32 bootloader | `0x22` |
| STM32 device ID | `0x0410` |

The current in-repo firmware engineering path is centered around:

```text
firmware/build-qualified-inrepo.py
firmware/tooling/packet-rssi-build-manifest.json
firmware/tooling/qualified-toolchain.json
firmware/vendor/ywd-mmdvm/
```

The byte-identical build environment is recorded in
`firmware/tooling/qualified-toolchain.json`; the qualified ARM compiler is GCC
`14.2.1`. CI pins the relevant Debian Trixie build environment and toolchain
versions used to reproduce the accepted image.

### YWD-1278 provenance anchor

The historical qualified YWD-1278 core is pinned at:

```text
c28c46c3478d7931af611923c92cd8f692a00858
```

The repository keeps that submodule and verifies the pin during installation as
part of the project's qualification/provenance chain. The active product package
contains its frozen runtime support under `src/ywd1278`, and the product HAT
probe/control and deterministic firmware-build support are carried directly in
this repository. This keeps normal YWD-MMDVM-TNC operation self-contained while
preserving traceability to the original engineering lineage.

The passive monitor feature is implemented only in the `src/ywdtnc/` product
layer around those frozen boundaries. Its physical qualification did not change
the 33-file `src/ywd1278/` runtime closure or the accepted firmware binary.

## Troubleshooting

### Service will not stay running

Start with:

```bash
sudo systemctl status ywd-mmdvm-tnc.service --no-pager
sudo journalctl -u ywd-mmdvm-tnc.service -n 100 --no-pager
```

Common causes include an unexpected HAT firmware identity, an invalid RF profile,
or another process holding the modem UART.

### Check the current RF profile

```bash
ywd-tnc-profile status
```

If you want a safe receive-only state while troubleshooting:

```bash
sudo ywd-tnc-profile rx-only
```

### Check whether KISS and the monitor are listening

```bash
sudo ss -ltnp | grep -E ':(8001|8002)\b'
```

For local-only mode, expect KISS on `127.0.0.1:8001`. When the monitor is
enabled, expect `127.0.0.1:8002`.

### A remote KISS client cannot connect

Check all three of these:

- `[kiss].listen` is `0.0.0.0`;
- `[kiss].allow_wildcard_bind` is `true`;
- the host/network firewall permits the chosen KISS TCP port from the trusted LAN.

Do not configure the remote application to connect to `0.0.0.0`.

### Packet logger is not writing traffic

Check the service and monitor endpoint:

```bash
sudo systemctl status ywd-packetlog.service --no-pager -l
sudo journalctl -u ywd-packetlog.service -n 100 --no-pager
sudo ss -ltnp | grep ':8002'
```

Then confirm the current configuration contains an enabled `[monitor]` block.
The logger reconnects automatically if `ywd-tncd` is restarted.

### Watch traffic without the logger

```bash
nc 127.0.0.1 8002
```

If that connection succeeds but no records appear, generate or wait for new RF
traffic. The monitor is intentionally live-only and has no replay buffer.

### Firmware probe says the UART is busy

`ywd-mmdvm-tnc.service` normally owns the UART. Stop it before manually running
`firmware/probe.sh` or low-level firmware maintenance tools, then start it again
when finished.

### Detailed installer logs

Installer and maintenance logs are kept under:

```text
/var/log/ywd-mmdvm-tnc/
```

Packet activity logs, when enabled, are kept under:

```text
/var/log/ywd-packetlog/
```

## Safety model

YWD-MMDVM-TNC intentionally fails closed around the two things most likely to
cause trouble: **RF transmit authority** and **firmware writes**.

- RF TX defaults disabled.
- TX must match one of the explicitly permitted product profiles.
- 145.050 MHz / power-200 is the historical physically-qualified packet TX profile.
- 144.390 MHz / power-200 is an explicitly permitted APRS operational profile, not a separate historical physical qualification claim.
- Arbitrary transmit frequencies and power values are rejected.
- Installation does not silently enable TX.
- Firmware writes are never automatic or silent.
- A verified stock rollback image is required before a stock-to-product write.
- Programmed firmware is independently read back and verified.
- STM32 option bytes are never written.
- One process owns the HAT UART.
- KISS and AGW clients receive live frames only; historical RF packets are not replayed to new clients.
- The monitor stream has no command API and no transmit API.
- Monitor subscribers have bounded queues; slow observers drop monitor events rather than backpressuring RF/KISS/AGW processing.
- The packet logger is read-only and writes only its log directory.
- Connected-mode retries/session state remain in the external packet stack, not in the modem service.

These software safeguards do not replace the operator's responsibility to obey
applicable amateur-radio rules and good RF engineering practice.

## Qualification

The stable product lineage has passed physical tests for:

- live Bell-202 / AX.25 receive on the qualified HAT;
- one-shot TCP KISS transmit with independent over-air decode;
- RX recovery on the same KISS connection after TX;
- no automatic modem-layer TX retry;
- remote LAN TCP KISS operation;
- real LinBPQ bidirectional interoperability;
- sustained connected-mode traffic and multi-frame transfers;
- the complete fresh-stock-HAT public installation path, including one-command bootstrap, guided configuration, reproducible non-root firmware build, exact artifact verification, protected two-pass stock backup, explicit flash confirmation, stock-to-qualified firmware deployment, service startup, and working LinBPQ TX/RX afterward;
- the accepted-firmware explicit update/reflash path;
- the passive monitor-event stream and first-class `ywd-packetlog` service on a running Pi/HAT/XRouter system with real RF RX and real transmitted AX.25 traffic.

The monitor-events physical gate captured **48 new schema-1 events** and **13
real RX events** while XRouter operated normally. It proved the ordered TX
lifecycle:

```text
tx.submitted
-> tx.queued
-> tx.channel_clear
-> tx.dispatched
-> tx.complete
```

The qualified request included a real channel-clear RSSI sample, positive
selector count, valid persisted JSONL, and `TX_AUTOMATIC_RETRY_ADDED=NO`.
Representative traffic included UI beaconing plus live `RR`, `I`, `DISC`, and
`UA` connected-mode exchanges through the normal packet network.

Machine-readable evidence is kept under [`qualification/`](qualification/), and
historical `checkpoint/*` branches preserve important qualification milestones.
Key records include:

```text
qualification/public-stock-hat-install-physical-2026-09-07.json
qualification/monitor-events-host-2026-09-10.json
qualification/monitor-events-physical-2026-09-10.json
```

The project deliberately distinguishes **physically qualified**, **host/CI
qualified**, and merely **permitted** behavior. A green CI result is not used as
a substitute for over-air hardware qualification.

## Repository layout

```text
.github/workflows/    CI and qualification contracts
config/               example product configuration
docs/                 focused operator/integration documentation
firmware/             accepted firmware registry, build, probe and flash tooling
installer/            guided installer and installation helpers
qualification/        machine-readable host and physical qualification evidence
scripts/              qualification and maintenance helpers
src/ywdtnc/           YWD-MMDVM-TNC product layer, monitor stream and packet logger
src/ywd1278/          frozen in-repo modem/runtime support
systemd/              TNC and packet-logger service definitions
tests/                host-side unit and contract tests
vendor/ywd-1278/      pinned historical qualification/provenance submodule
```

Development occurs on `dev` and focused feature/testing branches. `main` is the
stable public installation branch used by the one-line installer. Historical
qualification checkpoint branches are intentionally retained as evidence rather
than treated as normal development branches.

## License

Host-side YWD code is licensed **GPL-2.0-or-later**. Firmware derived from
MMDVM_HS retains its applicable upstream GPL notices and attribution.

See [`LICENSE`](LICENSE), [`LICENSING.md`](LICENSING.md), and the pinned
vendor/provenance sources for details.
