# YWD-MMDVM-TNC

**YWD-MMDVM-TNC** is a dedicated 1200-baud AX.25 modem/TNC appliance for Raspberry Pi systems fitted with a supported MMDVM_HS-style radio HAT. The service executable is **`ywd-tncd`**.

This project intentionally stops at the modem boundary. It does **not** provide a BBS, mailbox, packet node, connected terminal personality, beacon scheduler, or forwarding service. Applications such as LinBPQ/BPQ32 own those layers.

## Architecture

```text
 LinBPQ / BPQ32 / APRS and packet applications
              |                 |
         TCP KISS :8001    AGW raw :8000
              \                 /
               \               /
                  ywd-tncd
                     |
          AX.25 FCS + Bell-202 modem
          p-persistence / DCD / TXDELAY
                     |
             single UART owner
                     |
       YWD AX25R4 MMDVM_HS firmware
                     |
              STM32 + ADF7021
                     |
                     RF
```

Exactly one process owns the HAT UART. KISS and AGW share one RX decoder, one bounded TX admission queue, one channel-access policy, and one half-duplex RF path. Protocol clients receive live RF frames only; reconnecting never replays packet history.

The application above the modem owns callsigns, connected-mode AX.25 state, FRACK/retries, MAXFRAME, digipeating, nodes, BBS/mailbox behavior, forwarding, and user applications.

## Current development status

`dev` contains the first host-qualified modem-only foundation (P0). GitHub CI verifies the exact qualified-core pin, package installation, product compilation, AGW framing/stream behavior, live-only protocol semantics, TX-disabled ingress rejection, architecture contracts, and an RF-inert framework self-test.

No new physical RF qualification is claimed yet. The configuration defaults to `tx_enabled = false`.

## Qualified core and firmware provenance

The RF-critical packet core is pinned as the `vendor/ywd-1278` git submodule at exactly:

- YWD-1278 commit: `c28c46c3478d7931af611923c92cd8f692a00858`
- source tree: `9c06dea088a30782674404f43d964b8317c128a3`

The initial firmware target is:

`mmdvm-hs-hat-stm32f103-simplex-14.7456-adf7021`

Qualified AX25R4 runtime identity:

`MMDVM_HS_Hat-YWD-1278-AX25R4-v0.1.0-alpha1 14.7456MHz ADF7021 FW based on CA6JAU GitID #7ff74ed`

Firmware SHA-256:

`b06fcbf0baa36e865198091cee27c66e1624ef08117ee685253a7a5613c7c616`

The proven foundation includes sustained Bell-202 RX, AX.25 framing/FCS, RSSI-based half-duplex channel access, contextual TXDELAY, bounded KISS DATA admission, and the RX_STOP -> TX -> RF-idle -> RX_START lifecycle.

The vendor repository contains historical YWD-1278 upper-layer code as part of its qualification lineage. `ywd-tncd` does not compose those services; its only `ywd1278.service` dependency is the sustained TNC runtime and the service package's lower-level RX/channel-access imports.

## TCP KISS

KISS is the primary/native interface. Port 0 supports DATA plus TXDELAY, PERSIST, SLOTTIME, and half-duplex operation.

Default listener:

```text
127.0.0.1:8001
```

An explicit RFC1918/private IPv4 address can be configured for LAN clients. Wildcard/public binds are rejected. KISS has no transport authentication, so LAN exposure should be limited to a trusted network.

## AGW network protocol

The initial AGW implementation is deliberately **raw-mode only**, aimed first at LinBPQ/BPQ32 and similar clients that use AGW as a raw AX.25 modem interface.

Implemented initial wire behavior:

- standard packed 36-byte AGW network header
- lowercase `k` to subscribe to raw RX monitoring
- uppercase `K` for raw AX.25 RX/TX
- split/coalesced TCP stream decoding
- bounded payload validation
- same shared TX admission/CSMA path as KISS

Default listener:

```text
127.0.0.1:8000
```

Full AGW connected-session emulation is **not** claimed by this initial version.

## Clone and development setup

The qualified modem core is a pinned submodule, so clone recursively:

```bash
git clone --recursive -b dev https://github.com/merberg-ai/ywd-mmdvm-tnc.git
cd ywd-mmdvm-tnc
```

For an existing clone:

```bash
git submodule update --init --recursive
```

Development venv:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install --no-deps ./vendor/ywd-1278
python -m pip install --no-deps -e .
python -m unittest discover -s tests -v
ywd-tncd --config config/ywd-mmdvm-tnc.example.toml --framework-self-test
```

The framework self-test validates configuration/package plumbing without opening the modem UART or transmitting RF.

## Raspberry Pi install

From a recursive checkout:

```bash
sudo ./installer/install.sh
```

The installer:

- verifies the vendor submodule is at the exact qualified commit;
- creates `/opt/ywd-mmdvm-tnc/venv`;
- installs the pinned core and `ywd-tncd`;
- creates a safe TX-disabled `/etc/ywd-mmdvm-tnc/config.toml` if none exists;
- installs the systemd unit;
- runs the RF-inert framework self-test;
- does **not** flash firmware;
- does **not** start the RF service automatically.

After reviewing the config:

```bash
sudo systemctl enable --now ywd-mmdvm-tnc.service
```

The systemd service conflicts with MMDVMHost-family services and the old `ywd-1278.service` so two processes cannot intentionally own the same HAT UART.

## Initial safety constraints

- RF TX defaults off.
- Enabling TX is currently restricted to the physically-qualified 145.050 MHz / power-200 profile.
- Firmware identity must exactly match the qualified AX25R4 image.
- Firmware flashing is never automatic.
- KISS/AGW default to loopback and only explicit private IPv4 binds are accepted.
- KISS/AGW protocol clients never receive stored packet history.

## Branches

- `main` — landing/stable checkpoints
- `dev` — active development

Physical qualification should occur on `dev`; promote to `main` only after the corresponding modem boundary is proven on the target HAT.

## Licensing

Host-side YWD code is GPL-2.0-or-later. Firmware derived from MMDVM_HS retains the applicable upstream GPL notices and attribution. See `LICENSING.md` and the pinned vendor source for exact provenance.
