"""Typed RX-only MMDVM radio setup used by live packet receive qualification.

This module intentionally exposes only the two normal MMDVM commands required
before the already-qualified YWD_RX capture path can run:

* SET_FREQ for one guarded simplex receive frequency;
* SET_CONFIG using the frozen RX-safe modem-I/O initialization profile.

It does not expose arbitrary configuration bytes and contains no YWD_RF TX
request builder or device I/O.
"""

from __future__ import annotations

import struct

from . import protocol


# Exact pinned profile used by the physically-qualified AX25R3 receive capture.
# Simplex, DMR enabled only to initialize normal ADF7021/interrupt IO, idle
# modem state, no packet TX request. This byte sequence itself never keys RF.
RX_MODEM_IO_CONFIG = bytes(
    (
        0x80,  # simplex
        0x02,  # DMR enable for normal IO initialization
        0x00,  # TX delay (unused by passive YWD_RX)
        0x00,  # STATE_IDLE
        0x00,
        120,   # CW level -> 30 after >>2; unused by receive-only path
        1,     # DMR color code; irrelevant to raw AX25 capture
        0,
        0,
        50,
        50,
        50,
        50,
    )
)


def validate_rx_frequency_hz(frequency_hz: int) -> int:
    """Apply the same frozen MMDVM_HS amateur-band/satellite guards."""

    hz = int(frequency_hz)
    allowed = (
        144_000_000 <= hz < 148_000_000
        or 219_000_000 <= hz < 225_000_000
        or 420_000_000 <= hz < 475_000_000
        or 842_000_000 <= hz < 950_000_000
    )
    if not allowed:
        raise ValueError("frequency is outside the qualified MMDVM_HS amateur-band guard ranges")
    if 145_800_000 <= hz <= 146_000_000 or 435_000_000 <= hz <= 438_000_000:
        raise ValueError("frequency is inside a protected satellite/ISS range")
    return hz


def set_rx_frequency_request(frequency_hz: int) -> bytes:
    """Build the exact normal MMDVM SET_FREQ request used before passive RX.

    RX and TX frequency fields are both set to the requested simplex frequency
    because that is the pinned MMDVM_HS wire layout. RF power is fixed to the
    minimum nonzero value (1/255), but this receive-only path has no TX command.
    """

    hz = validate_rx_frequency_hz(frequency_hz)
    payload = bytes((0x00,)) + struct.pack("<I", hz) + struct.pack("<I", hz) + bytes((1,))
    return protocol.build_frame(protocol.SET_FREQ, payload)


def arm_rx_modem_io_request() -> bytes:
    """Build the fixed SET_CONFIG request that arms modem IO in idle state."""

    return protocol.build_frame(protocol.SET_CONFIG, RX_MODEM_IO_CONFIG)
