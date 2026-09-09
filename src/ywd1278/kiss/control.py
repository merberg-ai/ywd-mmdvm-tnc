"""Pure KISS TNC control-plane state for YWD-1278.

0C-P6 makes classic KISS TNC parameters real without connecting KISS DATA to
any transmit path.  The control plane is intentionally host-only: it has no
modem, UART, serial, RF, GPIO, firmware, or concrete TX-broker dependency.

Only KISS port 0 is supported.  Supported parameter commands are TXDELAY,
PERSIST, SLOTTIME, and FULLDUPLEX.  FULLDUPLEX is constrained to zero because
the qualified hardware/runtime is simplex half-duplex.  DATA remains rejected
in this phase.

Every accepted parameter command atomically replaces one immutable session
snapshot and advances its generation.  Future DATA admission can capture a
``TNCTransmitContext`` once and keep those semantics even if the host changes
parameters while the frame is waiting in a bounded queue.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import threading
from typing import Any

from ywd1278.tx.csma import DEFAULT_MAX_WAIT_SECONDS, CSMAParameters
from ywd1278.tx.txdelay import KISS_TXDELAY_DEFAULT, TXDelayProfile, resolve_txdelay

from .framing import DATA, FULLDUPLEX, PERSIST, SLOTTIME, TXDELAY, KISSMessage
from .server import PacketEvent, RXOnlyBackend


KISS_PORT = 0
DEFAULT_PERSIST = 63
DEFAULT_SLOTTIME = 10
DEFAULT_FULLDUPLEX = 0


@dataclass(frozen=True)
class TNCParameterSnapshot:
    """Immutable authoritative KISS parameter state for one generation."""

    generation: int = 0
    txdelay: int = KISS_TXDELAY_DEFAULT
    persist: int = DEFAULT_PERSIST
    slottime: int = DEFAULT_SLOTTIME
    fullduplex: int = DEFAULT_FULLDUPLEX
    port: int = KISS_PORT

    @property
    def txdelay_profile(self) -> TXDelayProfile:
        return resolve_txdelay(self.txdelay)

    def csma_parameters(self, *, max_wait_seconds: float = DEFAULT_MAX_WAIT_SECONDS) -> CSMAParameters:
        return CSMAParameters(
            persist=self.persist,
            slot_time_10ms=self.slottime,
            max_wait_seconds=max_wait_seconds,
        )


@dataclass(frozen=True)
class TNCTransmitContext:
    """One immutable parameter capture for a future queued DATA request."""

    parameters: TNCParameterSnapshot
    txdelay_profile: TXDelayProfile
    csma_parameters: CSMAParameters


@dataclass(frozen=True)
class TNCControlCounters:
    kiss_messages_received: int
    kiss_parameter_updates: int
    kiss_parameter_rejections: int
    kiss_malformed_frames: int
    kiss_unknown_commands: int
    kiss_unsupported_ports: int
    kiss_full_duplex_rejected: int
    kiss_slot_time_rejected: int
    kiss_data_tx_rejected: int


@dataclass(frozen=True)
class TNCQueueAccounting:
    """Operator-facing normalization of the already-qualified access queue."""

    tx_queue_depth: int
    tx_queue_capacity: int
    tx_queue_accepted: int
    tx_invalid_rejections: int
    tx_queue_full_drops: int
    tx_dispatched: int
    tx_access_timeouts: int
    tx_downstream_failures: int

    @classmethod
    def from_access_snapshot(cls, snapshot: Any) -> "TNCQueueAccounting":
        """Map an AccessQueueSnapshot without importing the concrete scheduler."""

        return cls(
            tx_queue_depth=int(snapshot.queue_depth),
            tx_queue_capacity=int(snapshot.queue_capacity),
            tx_queue_accepted=int(snapshot.accepted_requests),
            tx_invalid_rejections=int(snapshot.invalid_rejections),
            tx_queue_full_drops=int(snapshot.queue_full_rejections),
            tx_dispatched=int(snapshot.dispatched_requests),
            tx_access_timeouts=int(snapshot.timed_out_requests),
            tx_downstream_failures=int(snapshot.downstream_failures),
        )


class ControlDisposition(str, Enum):
    PARAMETER_UPDATED = "parameter-updated"
    DATA_REJECTED = "data-rejected"
    MALFORMED = "malformed"
    UNSUPPORTED_PORT = "unsupported-port"
    UNKNOWN_COMMAND = "unknown-command"
    PARAMETER_REJECTED = "parameter-rejected"


@dataclass(frozen=True)
class TNCControlResult:
    disposition: ControlDisposition
    port: int
    command: int
    previous: TNCParameterSnapshot
    current: TNCParameterSnapshot
    reason: str

    @property
    def updated(self) -> bool:
        return self.disposition is ControlDisposition.PARAMETER_UPDATED


class TNCSessionState:
    """Thread-safe authoritative KISS parameter/control state.

    No method in this class accepts a transmit callback or sends a frame.
    ``capture_tx_context`` only snapshots policy for a future qualification
    boundary; P6 still rejects every inbound KISS DATA message.
    """

    def __init__(self, parameters: TNCParameterSnapshot | None = None) -> None:
        initial = parameters or TNCParameterSnapshot()
        if initial.port != KISS_PORT:
            raise ValueError("only KISS port 0 is supported")
        # Validate every initial policy through the same qualified primitives.
        resolve_txdelay(initial.txdelay)
        initial.csma_parameters()
        if initial.fullduplex != 0:
            raise ValueError("FULLDUPLEX must be 0 on the simplex half-duplex target")

        self._lock = threading.RLock()
        self._parameters = initial
        self._messages_received = 0
        self._parameter_updates = 0
        self._parameter_rejections = 0
        self._malformed_frames = 0
        self._unknown_commands = 0
        self._unsupported_ports = 0
        self._full_duplex_rejected = 0
        self._slot_time_rejected = 0
        self._data_tx_rejected = 0

    @property
    def snapshot(self) -> TNCParameterSnapshot:
        with self._lock:
            return self._parameters

    @property
    def counters(self) -> TNCControlCounters:
        with self._lock:
            return TNCControlCounters(
                kiss_messages_received=self._messages_received,
                kiss_parameter_updates=self._parameter_updates,
                kiss_parameter_rejections=self._parameter_rejections,
                kiss_malformed_frames=self._malformed_frames,
                kiss_unknown_commands=self._unknown_commands,
                kiss_unsupported_ports=self._unsupported_ports,
                kiss_full_duplex_rejected=self._full_duplex_rejected,
                kiss_slot_time_rejected=self._slot_time_rejected,
                kiss_data_tx_rejected=self._data_tx_rejected,
            )

    def capture_tx_context(
        self,
        *,
        max_wait_seconds: float = DEFAULT_MAX_WAIT_SECONDS,
    ) -> TNCTransmitContext:
        """Capture one immutable parameter generation for future DATA admission."""

        parameters = self.snapshot
        return TNCTransmitContext(
            parameters=parameters,
            txdelay_profile=parameters.txdelay_profile,
            csma_parameters=parameters.csma_parameters(max_wait_seconds=max_wait_seconds),
        )

    def note_malformed_stream_frames(self, count: int) -> None:
        """Account for frames discarded by the incremental KISS decoder."""

        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError("malformed stream frame count must be a non-negative integer")
        if not count:
            return
        with self._lock:
            self._malformed_frames += count

    def apply(self, message: KISSMessage) -> TNCControlResult:
        """Apply one decoded KISS message without any transmit side effect."""

        with self._lock:
            self._messages_received += 1
            previous = self._parameters

            if message.port != KISS_PORT:
                self._unsupported_ports += 1
                return self._result(
                    ControlDisposition.UNSUPPORTED_PORT,
                    message,
                    previous,
                    "only KISS port 0 is supported",
                )

            if message.command == DATA:
                self._data_tx_rejected += 1
                return self._result(
                    ControlDisposition.DATA_REJECTED,
                    message,
                    previous,
                    "KISS DATA transmit ingress remains disconnected in 0C-P6",
                )

            if message.command not in {TXDELAY, PERSIST, SLOTTIME, FULLDUPLEX}:
                self._unknown_commands += 1
                return self._result(
                    ControlDisposition.UNKNOWN_COMMAND,
                    message,
                    previous,
                    "unsupported KISS control command ignored",
                )

            if len(message.frame) != 1:
                self._malformed_frames += 1
                self._parameter_rejections += 1
                return self._result(
                    ControlDisposition.MALFORMED,
                    message,
                    previous,
                    "supported KISS parameter command requires exactly one payload byte",
                )

            value = message.frame[0]
            field: str
            if message.command == TXDELAY:
                # Byte range is already 0..255; resolve validates the qualified
                # whole-HDLC-flag TXDELAY policy before state can change.
                resolve_txdelay(value)
                field = "txdelay"
            elif message.command == PERSIST:
                field = "persist"
            elif message.command == SLOTTIME:
                if value == 0:
                    self._slot_time_rejected += 1
                    self._parameter_rejections += 1
                    return self._result(
                        ControlDisposition.PARAMETER_REJECTED,
                        message,
                        previous,
                        "SLOTTIME=0 is rejected because the qualified CSMA scheduler requires a non-zero clear slot",
                    )
                field = "slottime"
            else:
                if value != 0:
                    self._full_duplex_rejected += 1
                    self._parameter_rejections += 1
                    return self._result(
                        ControlDisposition.PARAMETER_REJECTED,
                        message,
                        previous,
                        "FULLDUPLEX must remain 0 on the simplex half-duplex target",
                    )
                field = "fullduplex"

            current = replace(
                previous,
                generation=previous.generation + 1,
                **{field: value},
            )
            # Revalidate the full resulting snapshot before publishing it.
            resolve_txdelay(current.txdelay)
            current.csma_parameters()
            self._parameters = current
            self._parameter_updates += 1
            return TNCControlResult(
                disposition=ControlDisposition.PARAMETER_UPDATED,
                port=message.port,
                command=message.command,
                previous=previous,
                current=current,
                reason=f"KISS parameter {field.upper()} updated atomically",
            )

    def _result(
        self,
        disposition: ControlDisposition,
        message: KISSMessage,
        parameters: TNCParameterSnapshot,
        reason: str,
    ) -> TNCControlResult:
        return TNCControlResult(
            disposition=disposition,
            port=message.port,
            command=message.command,
            previous=parameters,
            current=parameters,
            reason=reason,
        )


class TNCControlBackend(RXOnlyBackend):
    """RX publishing plus KISS parameter handling, with DATA still rejected."""

    def __init__(
        self,
        events: tuple[PacketEvent, ...] | list[PacketEvent] = (),
        *,
        session: TNCSessionState | None = None,
        history_capacity: int = 256,
        subscriber_queue_capacity: int = 64,
    ) -> None:
        super().__init__(
            events,
            history_capacity=history_capacity,
            subscriber_queue_capacity=subscriber_queue_capacity,
        )
        self.session = session or TNCSessionState()

    @property
    def control_snapshot(self) -> TNCParameterSnapshot:
        return self.session.snapshot

    @property
    def control_counters(self) -> TNCControlCounters:
        return self.session.counters

    def reject_client_message(self, message: KISSMessage) -> TNCControlResult:
        result = self.session.apply(message)
        # Preserve the historical generic RX-only backend accounting for all
        # messages that this new control plane does not accept as a parameter.
        if not result.updated:
            super().reject_client_message(message)
        return result

    def note_malformed_stream_frames(self, count: int) -> None:
        self.session.note_malformed_stream_frames(count)
