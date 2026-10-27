# YWD-MMDVM-TNC TODO / release queue

This document tracks work not yet promoted to the stable `main` branch.
The latest released baseline is `main` (promoted from `dev-webui` on 2026-10-27).
All new Firmware Studio work is isolated on `dev-fws`. The frozen,
physically-qualified modem/firmware runtime must remain untouched.

## Pre-main WebUI release

- [x] Passive schema-1 monitor, packet logger, and read-only WebUI passed recorded Pi/HAT physical gates (2026-09-10/11).
- [x] Review the `main...dev-webui` diff and GitHub host CI (2026-10-27).
- [x] Harden low-level/guided installer upgrades: preserve active WebUI state without enabling inactive installations; add host regression tests (2026-10-27).
- [x] Recheck the Raspberry Pi checkout and run all three release gates as the checkout owner (2026-10-27).
- [ ] Verify deployed modem, packet logger, and WebUI stay healthy after the release candidate is installed; no unapproved RF transmit or firmware flashing.
- [x] Review final CI and fast-forward `main` to qualified `dev-webui` commit 13465e9d; create checkpoint/dev-webui-main-promoted-2026-10-27 (2026-10-27).

## Firmware Studio development (dev-fws)

Full design and safe launch procedure: [Firmware Studio](docs/firmware-studio.md).

- [x] FM0.0 Create dev-fws from the qualified main release.
- [x] FM0.1 Create a separate hardware-inert, loopback-only web application.
- [x] FM0.2 Add password file setup, scrypt verifier, session TTL, login
  throttling, same-origin/CSRF protection and strict response headers.
- [x] FM0.3 Add a dark responsive landing page, locked operation tiles and
  browser-only status console.
- [x] FM0.4 Run host CI tests proving no firmware, modem, GPIO, shell or UART
  API is reachable from the browser.
- [x] FM0.5 Add opt-in specific RFC1918 listener for VPN/LAN development
  (public/wildcard binds prohibited; safe loopback default preserved).
- [x] FM0.6 Phone/PiVPN review of responsive on-demand Firmware Studio UI
  without installing it as a service (operator screenshot, 2026-10-27).
- [x] FM1.1 Authenticated, hardware-inert firmware reference inventory and
  verified on-disk backup catalog with explicit running-firmware unknown state.
- [x] FM1.2 Local, root-owned read-only Unix-socket inventory broker that
  checks peer UID and exposes no executable maintenance actions.
- [x] FM1.3 Root-only, real-TTY-confirmed two-pass full-flash backup worker
  for exact YWD AX25R4 and qualified stock identities. Stock and captured-YWD
  backup trust levels remain distinct; no browser maintenance API.
- [x] FM1.4 Operator Pi/PiVPN smoke test of read-only inventory and broker
  (2026-10-27): browser reported privileged-readonly-broker, one stored
  original MMDVM_HS_Hat-v1.6.1 backup (20260909-212957) as GOLDEN STOCK /
  VERIFIED. Running firmware remains not probed. No live backup, HAT reset,
  service stop, UART access, or firmware write was requested/performed.
- [x] FM1.5a Add optional TLS listener with Secure sessions, session/CSRF
  guarded read-only backup preflight, bounded job events, mobile progress
  view and host regression tests. This does not perform hardware backups.
- [x] FM1.5b.1 Operator confirmed HTTPS login, mobile UI and read-only
  preflight over PiVPN on 2026-10-27. Three metadata checks completed.
  Root broker was not running, so backup count was restricted/unknown.
- [ ] FM1.5b.2 Validate phone certificate trust (a working self-signed HTTPS
  connection alone does not establish trusted identity), then run the
  HTTPS preflight with the separate read-only inventory broker connected.
- [x] FM1.5b.3 Do not display zero golden-stock backups when root storage is
  restricted: report unknown until the broker verifies the on-disk catalog.
- [ ] FM1.5c Design, independently audit and physically qualify a narrowly
  privileged, browser-authorized **real** dual-read backup job; require
  transport trust, per-operation consent, exclusive UART/service ownership,
  service recovery and durable audit log. HTTP LAN mode must never trigger it.
- [ ] FM2 Verified recovery of backups and qualified firmware write.
- [ ] FM3 Pinned official stock firmware build and guarded flash.
- [ ] FM4 Progress, logs, service shutdown and production-quality polish.

## Deferred: JNOS 2.0q interoperability

**Status: TODO, intentionally paused on 2026-10-27.**
Canonical tracking: [Issue #1](https://github.com/merberg-ai/ywd-mmdvm-tnc/issues/1).

- JNOS default `PACLEN 256` can exceed the firmware's single-burst
  `MAX_SELECTORS=1920` constraint, so a mailbox greeting I-frame may not reach RF.
- Verified temporary workaround on JNOS: `ifconfig rf ax25 paclen 96` and
  `ifconfig rf ax25 maxframe 1`.
- [ ] Add a nonfatal, deterministic TX frame preflight with explicit diagnostics
  (AX.25 frame length, actual encoded selector count, physical selector cap, TXDELAY).
- [ ] Verify an oversized application KISS frame cannot fail-latch the entire
  modem/TNC runtime; preserve functioning small-frame sessions.
- [ ] Add acceptance tests for small/96-byte, larger ~180-byte, and oversize frames.
- [ ] Separately plan and qualify a firmware/protocol extension for larger
  frames (chunked upload or firmware-side HDLC encoding). Do not modify the
  qualified firmware as part of the passive WebUI promotion.

## Planned: safe stock firmware recovery / image import

**Status: TODO, not yet implemented. Do not attempt a firmware write as part
of the current WebUI release.**

The existing flash tool accepts only the exact qualified AX25R4 image; its
`probe`, `backup`, and `flash` modes do not provide stock-image restoration.
Stock rollback artifacts exist as verified 131072-byte
`original-flash.bin` plus `manifest.json` under
`/var/lib/ywd-mmdvm-tnc/firmware-backups/`.

- [ ] Provide an explicit `restore-stock` mode accepting a verified, physically
  qualified stock backup, without using the AX25R4 artifact validation path.
- [ ] Support a separately supplied or freshly compiled stock firmware binary
  only after validating the exact STM32F103 / MMDVM_HS target, flash bounds,
  firmware format, approved provenance, and operator confirmation; do **not**
  treat any arbitrary `.bin` file as implicitly compatible.
- [ ] Require backup preservation, bootloader MCU/device checks, deliberate
  confirmation, readback comparison, and explicit application restart.
- [ ] Handle recovery of a nonbooting application using the ROM bootloader
  without relying solely on its now-unavailable application identity.
- [ ] Keep automatic flashing off, option bytes untouched, and modem services
  under controlled stop/restart; define how a non-AX25R4 firmware impacts
  `ywd-mmdvm-tnc.service` (it cannot continue expecting the AX25R4 identity).
- [ ] Add host-only safety/negative tests before any physical flash
  qualification with an operator-approved stock image.

## Future experiment

- [ ] OLED status/logo/telemetry service on the supported HAT, isolated from
  modem UART ownership and RF operation. Confirm display controller and I2C
  integration separately before implementation.
