"""Fixed physical TX qualification profile for YWD-1278.

This module is deliberately narrower than the normal RX setup helper.  It
contains the exact simplex SET_FREQ profile used for guarded P13b physical
verification only:

* RX frequency = 145.050 MHz
* TX frequency = 145.050 MHz
* RF power byte = 200/255

The power value is not experimental: the frozen YWD-MMDVM AX25-5B independent
over-air qualification used the same 200/255 value at 145.050 MHz.  There is no
caller-supplied frequency or power argument here, so this helper cannot become
an arbitrary tuning/power surface.
"""

from __future__ import annotations

import struct

from . import protocol

P13B_TX_FREQUENCY_HZ = 145_050_000
P13B_TX_POWER = 200


def p13b_tx_frequency_request() -> bytes:
    """Build the exact fixed P13b simplex SET_FREQ request."""

    payload = (
        bytes((0x00,))
        + struct.pack("<I", P13B_TX_FREQUENCY_HZ)
        + struct.pack("<I", P13B_TX_FREQUENCY_HZ)
        + bytes((P13B_TX_POWER,))
    )
    return protocol.build_frame(protocol.SET_FREQ, payload)
