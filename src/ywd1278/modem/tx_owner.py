"""Narrow TX-capable extension of the frozen single-owner modem runtime.

The base :class:`ModemOwner` remains the RX-only owner physically qualified by
0B-P12a/P12b.  This subclass keeps the broker-facing selector-burst primitive
and adds one qualification-only fixed RF setup operation used by P13b-R2.

There is still no raw transact API, RF abort API, RF exit API, KISS dependency,
or channel-access policy here.  All device I/O still occurs on the inherited
single owner thread.
"""

from __future__ import annotations

from typing import cast

from . import protocol, tx_config
from .owner import ModemOwner, ModemTransport, _Call


class TXModemOwner(ModemOwner):
    """Single-UART owner with narrow typed TX qualification operations."""

    def apply_tx_qualification_profile(self, *, timeout: float | None = None) -> None:
        """Apply the fixed P13b 145.050 MHz / 200-of-255 SET_FREQ profile.

        The caller supplies no frequency or power value.  This operation exists
        only so guarded physical qualification can reuse the exact previously
        independently decoded AX25-5B RF level without exposing arbitrary
        SET_FREQ bytes or a generic power-control surface.
        """

        request = tx_config.p13b_tx_frequency_request()
        self._call("apply_tx_qualification_profile", request, timeout)  # type: ignore[arg-type]

    def transmit_selector_burst(
        self,
        selector_count: int,
        packed_selectors: bytes,
        *,
        timeout: float | None = None,
    ) -> None:
        """Submit one qualified ``YWD_RF/TX_TONES`` request through the owner.

        The public shape is typed: callers supply only the selector count and
        packed selector bytes.  They cannot supply an arbitrary modem frame.
        The bounded TX broker is the intended product caller for this method.
        """

        request = protocol.rf_tx_tones_request(
            selector_count=int(selector_count),
            packed_selectors=bytes(packed_selectors),
        )
        # _call is the inherited single-owner queue boundary.  The base class
        # annotates its argument narrowly because all historical operations
        # used int/None; bytes are safe here because this subclass owns the
        # matching dispatch branches below.
        self._call("transmit_selector_burst", request, timeout)  # type: ignore[arg-type]

    def _dispatch(self, transport: ModemTransport, call: _Call) -> object | None:
        if call.operation == "apply_tx_qualification_profile":
            request = cast(bytes, call.argument)
            response = self._transact(transport, request, call.timeout)
            protocol.parse_ack(response, expected_command=protocol.SET_FREQ)
            return None
        if call.operation == "transmit_selector_burst":
            request = cast(bytes, call.argument)
            response = self._transact(transport, request, call.timeout)
            protocol.parse_ack(response, expected_command=protocol.YWD_RF)
            return None
        return super()._dispatch(transport, call)
