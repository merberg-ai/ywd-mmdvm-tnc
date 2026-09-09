"""Host-side TXDELAY policy for Bell-202/AX.25 transmission.

KISS represents TXDELAY as one unsigned byte in 10 ms units.  YWD-1278 emits
an HDLC flag preamble rather than an unstructured key-up tone, so requested
TXDELAY is rounded *up* to a whole number of 8-bit flags.  Rounding upward
ensures the effective preamble is never shorter than requested.

The historically qualified :class:`~ywd1278.tx.broker.TXBroker` remains
unchanged with its fixed 45-flag P5 profile.  ``TXDelayBroker`` is the later
0C-P5 boundary: it inherits that broker's bounded queue, modem-busy preflight,
worker, accounting, and fail-closed behavior while varying only the opening
HDLC flag count used during frame preparation.

There is intentionally no runtime TXDELAY setter here.  A value is validated
and frozen when the broker instance is constructed.  Safe live parameter
mutation belongs to the later KISS-parameter phase.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib

from ywd1278.ax25 import verify_fcs
from ywd1278.modem import protocol
from ywd1278.phy import MARK, duration_seconds, frame_to_selectors, pack_selectors
from ywd1278.tx.broker import (
    P5_INITIAL_TONE,
    P5_POST_FLAGS,
    TXBroker,
    TXBrokerFrameRejected,
    TXModemPort,
    TXReceipt,
)

KISS_TXDELAY_MIN = 0
KISS_TXDELAY_MAX = 255
KISS_TXDELAY_DEFAULT = 30
KISS_TXDELAY_UNIT_SECONDS = 0.010
BELL202_BAUD = 1200
HDLC_FLAG_BITS = 8
HDLC_FLAG_SECONDS = HDLC_FLAG_BITS / BELL202_BAUD


@dataclass(frozen=True)
class TXDelayProfile:
    """Resolved whole-flag Bell-202 preamble for one KISS TXDELAY value."""

    units: int
    requested_seconds: float
    pre_flags: int
    effective_seconds: float
    rounding_overrun_seconds: float


def resolve_txdelay(units: int = KISS_TXDELAY_DEFAULT) -> TXDelayProfile:
    """Validate one KISS TXDELAY byte and resolve its HDLC flag preamble.

    A zero requested delay still requires one opening HDLC flag so the frame
    has a legal delimiter.  All positive delays are rounded upward to the next
    whole flag when they are not already exactly representable.
    """

    if isinstance(units, bool) or not isinstance(units, int):
        raise TypeError("TXDELAY must be an integer KISS parameter byte")
    if not (KISS_TXDELAY_MIN <= units <= KISS_TXDELAY_MAX):
        raise ValueError(
            f"TXDELAY must be {KISS_TXDELAY_MIN}..{KISS_TXDELAY_MAX} "
            "in 10 ms units"
        )

    # 10 ms at 1200 baud is exactly 12 selectors/bits.  Integer arithmetic
    # keeps the flag rounding deterministic and avoids float boundary issues.
    requested_selectors = units * 12
    pre_flags = max(1, (requested_selectors + HDLC_FLAG_BITS - 1) // HDLC_FLAG_BITS)
    effective_selectors = pre_flags * HDLC_FLAG_BITS

    requested_seconds = units * KISS_TXDELAY_UNIT_SECONDS
    effective_seconds = effective_selectors / BELL202_BAUD
    return TXDelayProfile(
        units=units,
        requested_seconds=requested_seconds,
        pre_flags=pre_flags,
        effective_seconds=effective_seconds,
        rounding_overrun_seconds=effective_seconds - requested_seconds,
    )


class TXDelayBroker(TXBroker):
    """Qualified TX broker behavior with one construction-time TXDELAY value.

    The parent broker remains the source of queueing, worker, timeout,
    preflight, and modem-submission semantics.  This subclass overrides only
    the deterministic frame-preparation step.
    """

    def __init__(
        self,
        owner: TXModemPort,
        *,
        txdelay_units: int = KISS_TXDELAY_DEFAULT,
        transmit_enabled: bool = False,
        queue_capacity: int = 4,
        submit_timeout: float = 0.05,
        default_transaction_timeout: float = 1.5,
        thread_name: str = "ywd1278-txdelay-broker",
    ) -> None:
        self._txdelay_profile = resolve_txdelay(txdelay_units)
        super().__init__(
            owner,
            transmit_enabled=transmit_enabled,
            queue_capacity=queue_capacity,
            submit_timeout=submit_timeout,
            default_transaction_timeout=default_transaction_timeout,
            thread_name=thread_name,
        )

    @property
    def txdelay_profile(self) -> TXDelayProfile:
        """Return the immutable construction-time TXDELAY profile."""

        return self._txdelay_profile

    def _prepare_frame(self, frame: bytes) -> tuple[TXReceipt, bytes]:
        if len(frame) < 3 or not verify_fcs(frame):
            raise TXBrokerFrameRejected("AX.25 frame must include a valid FCS")

        selectors = frame_to_selectors(
            frame,
            pre_flags=self._txdelay_profile.pre_flags,
            post_flags=P5_POST_FLAGS,
            initial_tone=P5_INITIAL_TONE,
        )
        selector_count = len(selectors)
        if selector_count > protocol.MAX_SELECTORS:
            raise TXBrokerFrameRejected(
                f"serialized frame exceeds modem selector limit: "
                f"{selector_count}>{protocol.MAX_SELECTORS}"
            )
        packed = pack_selectors(selectors)
        receipt = TXReceipt(
            frame_bytes=len(frame),
            frame_sha256=hashlib.sha256(frame).hexdigest(),
            selector_count=selector_count,
            packed_selector_bytes=len(packed),
            packed_selector_sha256=hashlib.sha256(packed).hexdigest(),
            nominal_duration_seconds=duration_seconds(selector_count),
        )
        return receipt, packed
