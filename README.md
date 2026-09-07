# YWD-MMDVM-TNC

**YWD-MMDVM-TNC** is a dedicated 1200-baud AX.25 modem/TNC appliance for Raspberry Pi systems fitted with the qualified MMDVM_HS-style radio HAT. The service executable is **`ywd-tncd`**.

The project deliberately stops at the modem boundary. It does **not** implement a BBS, mailbox, packet node, connected terminal personality, beacon scheduler, forwarding engine, or AX.25 retry state machine. Applications such as LinBPQ/BPQ32 own those layers.

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
       qualified YWD AX25R4 firmware
                     |
              STM32 + ADF7021
                     |
                     RF
```

Exactly one process owns the HAT UART. KISS and AGW share one RX decoder, one bounded TX admission queue, one channel-access policy, and one half-duplex RF path. Protocol clients receive live RF frames only; reconnecting never replays packet history.

## Development status

P1 is the **pre-RF machine-ready checkpoint**. The repo now includes the standalone/rebranded install, preflight, firmware build/verification, safe HAT probe, explicit qualified flash, and RX-only physical qualification tooling needed to move the new product onto the target Pi.

P1 host CI does **not** touch hardware or RF. The first new physical gate is intentionally RX-only:

```text
145.050 MHz RF -> AX25R4 HAT -> ywd-tncd -> TCP KISS -> ywd-tnc-rx-gate
```

The physical gate requires `radio.tx_enabled = false` and the RX test client never writes to the KISS socket.

## Qualified provenance

The RF-critical modem core remains pinned as `vendor/ywd-1278` at exactly:

- commit: `c28c46c3478d7931af611923c92cd8f692a00858`
- source tree: `9c06dea088a30782674404f43d964b8317c128a3`

Qualified hardware target:

`mmdvm-hs-hat-stm32f103-simplex-14.7456-adf7021`

Qualified AX25R4 runtime identity:

`MMDVM_HS_Hat-YWD-1278-AX25R4-v0.1.0-alpha1 14.7456MHz ADF7021 FW based on CA6JAU GitID #7ff74ed`

Exact qualified firmware:

- size: `59892` bytes
- SHA-256: `b06fcbf0baa36e865198091cee27c66e1624ef08117ee685253a7a5613c7c616`
- flash base: `0x08000000`
- STM32 bootloader: `0x22`
- STM32 device ID: `0x0410`

Qualified stock rollback baseline:

- full main-flash size: `131072` bytes
- SHA-256: `4981b35b2d50ada0b09322d9de19dd58a0cbd49eb005693499d1acae92f9d684`

The firmware binary intentionally keeps its **YWD-1278-AX25R4** runtime identity. That exact identity and binary are already physically qualified. Renaming the firmware string merely for cosmetics would create a different binary and throw away the strongest qualification evidence we have. The new host product, service, paths, scripts, state, commands, and documentation are branded YWD-MMDVM-TNC / `ywd-tncd`.

## Interfaces

### TCP KISS

Primary/native interface, port 0. DATA plus TXDELAY, PERSIST and SLOTTIME are supported. Default listener:

```text
127.0.0.1:8001
```

### AGW raw

Initial AGW support is deliberately raw-mode only. Implemented wire behavior includes the standard 36-byte AGW header, lowercase `k` raw-monitor subscription and uppercase `K` raw AX.25 receive/transmit. Default listener:

```text
127.0.0.1:8000
```

KISS and AGW have no transport authentication. The config accepts loopback or an explicit private IPv4 address and rejects wildcard/public binds.

## Clone

Clone recursively so the exact qualified core is present:

```bash
git clone --recursive -b dev https://github.com/merberg-ai/ywd-mmdvm-tnc.git
cd ywd-mmdvm-tnc
```

For an existing checkout:

```bash
git checkout dev
git pull --ff-only
git submodule update --init --recursive
```

## Host development / CI-equivalent check

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install --no-deps ./vendor/ywd-1278
python -m pip install --no-deps -e .
bash scripts/check-p1-pre-rf.sh
```

The P1 host contract verifies the exact submodule pin, Python/shell syntax, firmware safety profile, modem-only unit tests, RX-gate no-write behavior, and the RF-inert daemon framework self-test.

## Raspberry Pi machine setup

The supported bootstrap currently targets Raspberry Pi OS / Debian-family systems. Qualified automatic HAT bootloader GPIO control is anchored to the Raspberry Pi 5 target used during YWD-1278 qualification.

From the repo checkout:

```bash
sudo ./installer/bootstrap.sh
```

The bootstrap installs the Python/runtime tools plus the ARM firmware build and STM32 programming dependencies, initializes the exact submodule, and calls the product installer. It **does not flash firmware, start the modem service, or enable RF TX**.

If dependencies are already installed, the smaller installer is:

```bash
sudo ./installer/install.sh
```

Installed layout:

```text
/opt/ywd-mmdvm-tnc/source
/opt/ywd-mmdvm-tnc/venv
/etc/ywd-mmdvm-tnc/config.toml
/var/lib/ywd-mmdvm-tnc
/etc/systemd/system/ywd-mmdvm-tnc.service
```

Installed commands:

```text
ywd-tncd
ywd-tnc-fw
ywd-tnc-rx-gate
```

A new config is created with `tx_enabled = false`. Existing config is preserved and the installer refuses to start the RF service automatically.

## Machine preflight

With the HAT UART idle:

```bash
YWD_TNC_PREFLIGHT_FULL_TOOLCHAIN=1 ./scripts/preflight.sh
```

This verifies the exact core pin, Python version, target Pi, `/dev/ttyAMA0`, idle UART, TX-disabled config, and the complete firmware/flash toolchain. It does not open the modem UART.

## Safe HAT identity probe

Stop any old MMDVMHost/YWD-1278 process that owns `/dev/ttyAMA0`, then:

```bash
sudo ./firmware/probe.sh
```

The P1 probe sends only GET_VERSION and requires the exact AX25R4 target identity. It does not configure RF, start RX, request TX, write flash, or touch option bytes.

If this passes on a HAT that already has the physically qualified AX25R4 firmware installed, **no firmware flash is needed for the P1 RX test**.

## Reproducible qualified firmware build

Firmware builds are intentionally non-root:

```bash
./firmware/build.sh
```

The wrapper verifies the exact core commit, runs the frozen YWD-1278 deterministic AX25R4 builder twice, requires byte-for-byte reproducibility, and verifies the final 59,892-byte artifact against the product SHA-256. Building never accesses the HAT, GPIO, flash, or RF.

Inspect the product firmware contract without hardware access:

```bash
ywd-tnc-fw --profile firmware/product-ax25r4.json show
```

## Explicit qualified firmware tool

Firmware deployment is **never part of installation or service startup**.

Safe identity probe through the firmware tool:

```bash
sudo ./firmware/flash.sh probe
```

Capture a protected stock rollback image while exact stock firmware is running:

```bash
sudo ./firmware/flash.sh backup
```

Program/verify the exact qualified image only when needed:

```bash
sudo ./firmware/flash.sh flash --authorize FLASH-QUALIFIED-AX25R4
```

A write additionally requires typing:

```text
WRITE-FIRMWARE-NOW
```

The flash path requires the exact artifact, exact target/STM32 bootloader identity, a verified two-pass golden-stock rollback backup before any main-flash write, independent programmed readback, and exact post-operation runtime identity. Option-byte operations are never issued. If the exact qualified AX25R4 firmware is already running, flash mode verifies programmed bytes without rewriting main flash.

For migration safety, an old `/var/lib/ywd-1278/firmware-backups/...` backup may be reused only if the new verifier independently proves it has the exact target, geometry, two-pass evidence, and golden stock SHA-256.

## P1 physical RX qualification

Keep `/etc/ywd-mmdvm-tnc/config.toml` at:

```toml
[radio]
frequency_mhz = 145.050
tx_power = 200
tx_enabled = false

[kiss]
enabled = true
listen = "127.0.0.1"
port = 8001
```

The easiest physical gate is:

```bash
sudo ./scripts/p1-rx-physical.sh
```

That wrapper refuses anything except the exact 145.050/TX-disabled/loopback-KISS profile, starts `ywd-mmdvm-tnc.service` if needed, and invokes the receive-only KISS gate. When prompted, transmit **one normal 1200-baud AX.25 packet on 145.050 MHz from another station/radio**.

Success includes:

```text
YWD_TNC_P1_LIVE_RX=PASS
KISS_BYTES_SENT=0
TX_REQUESTED=NO
PHYSICAL_GATE_RF_DIRECTION=RX_ONLY
YWD_TNC_P1_PHYSICAL_RX=PASS
```

The decoded source, destination, path, frame type, PID, information text and raw frame hex are printed as qualification evidence.

For manual two-terminal operation instead:

```bash
sudo systemctl start ywd-mmdvm-tnc.service
sudo journalctl -fu ywd-mmdvm-tnc.service
```

Then in another terminal:

```bash
ywd-tnc-rx-gate --timeout 120
```

Do **not** enable TX for P1. TX-through-KISS becomes a separate checkpoint only after this new product boundary has independently passed RX.

## Service ownership

`ywd-mmdvm-tnc.service` conflicts with MMDVMHost-family services and the old `ywd-1278.service`, preventing intentional simultaneous ownership of the HAT UART. The installer does not disable those services behind the operator's back; starting `ywd-mmdvm-tnc.service` is the explicit handoff point.

## Safety constraints

- RF TX defaults off.
- P1 physical qualification is RX-only.
- Enabling product TX remains restricted in code to the previously qualified 145.050 MHz / power-200 profile.
- Firmware runtime identity must exactly match the qualified AX25R4 image.
- Firmware flashing is never automatic.
- Installation never flashes firmware or starts the RF service.
- Actual firmware write requires exact artifact, stock rollback proof, explicit authorization and typed confirmation.
- Programmed bytes are independently read back and verified.
- Option bytes are never written.
- KISS/AGW clients never receive stored packet history.

## Branch policy

- `main` — last promoted/stable checkpoint
- `dev` — active development
- `checkpoint/*` — exact qualification/handoff tips

P1 pre-RF is staged and qualified on `dev` first. `main` should not claim the physical P1 RX result until the target Pi/HAT actually passes the over-air gate.

## Licensing

Host-side YWD code is GPL-2.0-or-later. Firmware derived from MMDVM_HS retains the applicable upstream GPL notices and attribution. See `LICENSING.md` and the pinned vendor source for exact provenance.
