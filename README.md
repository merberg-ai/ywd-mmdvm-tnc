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

**P2 TCP-KISS TX and same-connection RX recovery are physically qualified.** On 2026-09-07, the target Raspberry Pi 5 / MMDVM_HS HAT passed a one-shot transmit qualification at 145.050 MHz / power 200 using exact product commit `c9e1cfe051326643eb16093f79e65fa272d29796`.

The transmitted application frame was exactly:

```text
KJ6YWD-10>YWD127:YWD-MMDVM-TNC P2 1/1
```

An independent over-air receiver decoded that exact UI/PID `0xF0` frame once. Product accounting independently proved one TCP-KISS request, one admission, one queue dispatch and one runtime RF dispatch. After a three-second hold the dispatch count remained exactly one, proving no automatic internal retry.

The same TCP KISS connection then received fresh live RF after TX. The formal return frame decoded as `KJ6YWD>JIM,KRDG,KBANN,KJOHN,KBULN,WOODY` UI/PID `0xF0` with information `yooooooooo hellooooooo`. RX had in fact already resumed before that formal return gate: the harness drained 18 KISS bytes of intervening traffic and its decoded-RX baseline had advanced to one.

Persistent `/etc/ywd-mmdvm-tnc/config.toml` remained TX-disabled and unmodified; P2's TX authority existed only in memory for the qualification process. The full machine-readable record is `qualification/p2-kiss-tx-physical-2026-09-07.json`.

**P1 RX remains physically qualified.** Its full machine-readable record is `qualification/p1-rx-physical-2026-09-07.json`.

The physically proven product boundary is now bidirectional:

```text
TCP KISS -> ywd TNCEngine -> qualified AX25R4 HAT -> 145.050 MHz RF
145.050 MHz RF -> qualified AX25R4 HAT -> same TNCEngine -> same TCP KISS connection
```

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
bash scripts/check-p2-pre-rf.sh
```

The P2 host contract includes the full inherited P1 regression suite and verifies the exact submodule pin, Python/shell syntax, firmware safety profile, one-shot P2 client shape, persistent TX-disabled default, zero automatic retry path, and the RF-inert daemon framework self-test. CI never opens the modem UART or transmits RF.

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
ywd-tnc-p2-gate
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

The probe sends only GET_VERSION and requires the exact AX25R4 target identity. It does not configure RF, start RX, request TX, write flash, or touch option bytes.

If this passes on a HAT that already has the physically qualified AX25R4 firmware installed, no firmware flash is needed for product qualification.

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

P1's historical RX-only gate is retained for reproducibility. Keep `/etc/ywd-mmdvm-tnc/config.toml` at 145.050 MHz with `tx_enabled = false`, then run:

```bash
sudo ./scripts/p1-rx-physical.sh
```

Success includes `YWD_TNC_P1_LIVE_RX=PASS`, `KISS_BYTES_SENT=0`, `TX_REQUESTED=NO`, `PHYSICAL_GATE_RF_DIRECTION=RX_ONLY`, and `YWD_TNC_P1_PHYSICAL_RX=PASS`.

## P2 physical one-shot TCP-KISS TX qualification

P2 also requires the persistent config to remain TX-disabled. The qualification harness grants temporary in-memory TX authority only for the previously qualified 145.050 MHz / power-200 profile.

Run:

```bash
sudo ./scripts/p2-kiss-tx-physical.sh
```

The operator must explicitly type `P2-TX-ONCE-145050`. The client then issues exactly one TCP-KISS DATA request for:

```text
KJ6YWD-10>YWD127:YWD-MMDVM-TNC P2 1/1
```

The physical gate requires one independent over-air decode of that exact frame, proves queue/runtime dispatch counts remain exactly one through a hold period, and finally requires fresh RF receive on the same TCP KISS connection after TX. It does not modify the persistent config and has no client retry loop.

The 2026-09-07 physical run passed all of those gates. See `qualification/p2-kiss-tx-physical-2026-09-07.json` for exact counters, raw frame hex, independent receiver evidence, and post-TX RX evidence.

## Service ownership

`ywd-mmdvm-tnc.service` conflicts with MMDVMHost-family services and the old `ywd-1278.service`, preventing intentional simultaneous ownership of the HAT UART. The installer does not disable those services behind the operator's back; starting `ywd-mmdvm-tnc.service` is the explicit handoff point.

## Safety constraints

- Persistent RF TX defaults off.
- P1 physical qualification is RX-only.
- P2 physical TX authority is one-shot, explicit, temporary and in-memory only.
- Product TX remains restricted in code to the physically qualified 145.050 MHz / power-200 profile.
- The P2 client has no automatic retry; one admitted request must produce at most one dispatch.
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

P1 RX and P2 one-shot TCP-KISS TX/same-connection RX recovery have passed the target Pi/HAT over-air gates. `checkpoint/p1-rx-physical-qualified` identifies the P1 evidence-bearing tip; `checkpoint/p2-kiss-tx-pre-rf` preserves the exact P2 code-under-test tip. The evidence-bearing P2 physical checkpoint is pinned separately after exact-tip CI validates this record.

## Licensing

Host-side YWD code is GPL-2.0-or-later. Firmware derived from MMDVM_HS retains the applicable upstream GPL notices and attribution. See `LICENSING.md` and the pinned vendor source for exact provenance.
