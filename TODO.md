# YWD-MMDVM-TNC TODO / release queue

This document tracks work not yet promoted to the stable `main` branch.
The current qualified baseline is `dev-webui` and the frozen, physically-qualified
modem firmware/runtime must remain untouched by passive WebUI release work.

## Pre-main WebUI release

- [x] Passive schema-1 monitor, packet logger, and read-only WebUI passed recorded Pi/HAT physical gates (2026-09-10/11).
- [x] Review the `main...dev-webui` diff and GitHub host CI (2026-10-27).
- [ ] Harden guided installer upgrades: when `ywd-webui.service` was already active, stop it before replacing the installed package and restore that running state afterward without auto-enabling WebUI for other installations; add regression coverage.
- [ ] Recheck the Raspberry Pi checkout and run all release gates as the normal checkout owner; fix local submodule/bytecode cache ownership before interpreting those failures as source regressions.
- [ ] Verify deployed modem, packet logger, and WebUI stay healthy after the release candidate is installed; no unapproved RF transmit or firmware flashing.
- [ ] Review final CI and only then fast-forward `main` from the approved candidate.

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

## Future experiment

- [ ] OLED status/logo/telemetry service on the supported HAT, isolated from
  modem UART ownership and RF operation. Confirm display controller and I2C
  integration separately before implementation.
