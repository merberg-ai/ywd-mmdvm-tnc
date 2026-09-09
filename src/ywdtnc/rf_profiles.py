"""Product-level RF profiles layered over the frozen qualified modem core.

The pinned YWD-1278 runtime intentionally exposes only its historical fixed
145.050 MHz TX qualification helper. YWD-MMDVM-TNC needs a slightly broader,
still bounded application-facing surface for interoperability testing. This
module keeps that extension in the product layer: callers may select only the
explicitly permitted frequency/power pairs declared by :mod:`ywdtnc`.
"""

from __future__ import annotations

import struct
from typing import cast

from ywd1278.modem import protocol
from ywd1278.modem.owner import ModemTransport, _Call
from ywd1278.modem.tx_owner import TXModemOwner

from . import PERMITTED_TX_PROFILES


def validate_tx_profile(frequency_hz: int, power: int) -> tuple[int, int]:
    """Return one explicitly permitted simplex TX profile or fail closed."""

    profile = (int(frequency_hz), int(power))
    if profile not in PERMITTED_TX_PROFILES:
        raise ValueError(
            "unsupported TX profile; permitted profiles are "
            "145.050 MHz / power-200 and 144.390 MHz / power-200"
        )
    return profile


def build_tx_profile_request(frequency_hz: int, power: int) -> bytes:
    """Build a normal MMDVM simplex SET_FREQ request for a permitted profile."""

    frequency_hz, power = validate_tx_profile(frequency_hz, power)
    payload = (
        bytes((0x00,))
        + struct.pack("<I", frequency_hz)
        + struct.pack("<I", frequency_hz)
        + bytes((power,))
    )
    return protocol.build_frame(protocol.SET_FREQ, payload)


class ProductTXModemOwner(TXModemOwner):
    """Frozen modem owner plus a bounded product-level TX profile operation."""

    def apply_tx_profile(
        self,
        frequency_hz: int,
        power: int,
        *,
        timeout: float | None = None,
    ) -> None:
        request = build_tx_profile_request(frequency_hz, power)
        # TXModemOwner already uses this private single-owner queue boundary for
        # its fixed qualification request. We preserve that ownership model and
        # add no raw transaction API.
        self._call("apply_product_tx_profile", request, timeout)  # type: ignore[arg-type]

    def _dispatch(self, transport: ModemTransport, call: _Call) -> object | None:
        if call.operation == "apply_product_tx_profile":
            request = cast(bytes, call.argument)
            response = self._transact(transport, request, call.timeout)
            protocol.parse_ack(response, expected_command=protocol.SET_FREQ)
            return None
        return super()._dispatch(transport, call)
