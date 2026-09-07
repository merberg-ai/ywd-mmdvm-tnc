# YWD-MMDVM-TNC

**YWD-MMDVM-TNC** is a dedicated 1200-baud AX.25 modem/TNC appliance for Raspberry Pi systems fitted with a supported MMDVM_HS-style radio HAT.

The service executable is **`ywd-tncd`**.

This project intentionally stops at the modem/TNC boundary. It does **not** provide a BBS, mailbox, packet node, terminal personality, beacon scheduler, or forwarding service. Those belong in applications such as LinBPQ/BPQ32 or any other software that can use KISS or the AGW network protocol.

## Intended architecture

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

## Design rules

- One process owns the modem UART.
- KISS and AGW share one RX decoder, one TX admission queue, one CSMA/channel-access policy, and one RF path.
- Protocol clients receive **live RF frames only**; reconnecting must never replay historical packets.
- The application above the modem owns callsigns, connected-mode AX.25 state, retries, digipeating, nodes, BBS/mailbox behavior, forwarding, and user applications.
- KISS and AGW listeners default to loopback. Private-LAN binding must be explicit; neither protocol provides transport security or authentication.
- Firmware flashing is never automatic.
- RF-critical code imported from YWD-1278 remains traceable to its qualified source lineage.

## Qualified foundation

The initial modem core is derived from the tested YWD-1278 `dev` tree at commit:

`c28c46c3478d7931af611923c92cd8f692a00858`

The corresponding source tree is:

`9c06dea088a30782674404f43d964b8317c128a3`

The initial firmware target is the YWD AX25R4 MMDVM_HS simplex/ADF7021 image with runtime identity:

`MMDVM_HS_Hat-YWD-1278-AX25R4-v0.1.0-alpha1 14.7456MHz ADF7021 FW based on CA6JAU GitID #7ff74ed`

The proven modem foundation includes sustained Bell-202 RX, AX.25 framing/FCS, RSSI-based half-duplex channel access, contextual TXDELAY, bounded KISS DATA admission, and the RX_STOP -> TX -> RF-idle -> RX_START lifecycle.

## Interface plan

### TCP KISS

KISS is the primary/native interface. Port 0 supports DATA plus TXDELAY, PERSIST, SLOTTIME, and half-duplex operation. The listener defaults to `127.0.0.1:8001` and may be explicitly bound to a trusted LAN address.

### AGW network protocol

The first AGW implementation is deliberately **raw-mode only**, aimed at LinBPQ/BPQ32 and other clients that use AGW as a raw AX.25 modem interface. Initial scope is lowercase `k` raw-monitor subscription and uppercase `K` raw AX.25 send/receive over the standard 36-byte AGW header.

Full AGW connected-session emulation is not claimed by the initial implementation.

## Status

Repository bootstrap is in progress. Until an initial qualification checkpoint is published, treat `main` as development software and keep RF transmission disabled unless deliberately testing the qualified hardware/profile.

## Licensing and provenance

Host-side YWD code is GPL-2.0-or-later. Firmware derived from MMDVM_HS retains its upstream GPL notices and attribution. See `LICENSING.md` for source lineage and redistribution notes.
