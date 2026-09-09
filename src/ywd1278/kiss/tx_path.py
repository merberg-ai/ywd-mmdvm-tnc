"""Guarded KISS DATA admission for YWD-1278 0C-P7.

P7 is the first boundary where a port-0 KISS DATA message may become a bounded
transmit request.  It still does not own a modem, UART, RF device, clock, RNG,
or TX broker.  Incoming KISS DATA contains an AX.25 frame body without FCS;
this module validates the AX.25 body, captures the immutable P6 TNC parameter
context supplied by the caller, appends the AX.25 FCS exactly once, and queues
the request behind the already-qualified P2/P1 channel-access policy.

The historical P4a queue remains frozen.  This P7 queue intentionally mirrors
its fail-closed semantics while adding per-request P6 context:

* bounded queue with total lifetime measured from admission;
* immutable TXDELAY/PERSIST/SLOTTIME generation captured at admission;
* only the current head request advances on one RSSI observation;
* fresh channel-access attempt per head request using that request's captured
  PERSIST/SLOTTIME values;
* READY dispatches exactly once through an injected contextual submitter;
* timeout or downstream failure is terminal with no automatic retry.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Protocol

from ywd1278.ax25 import append_fcs, parse_frame
from ywd1278.tx.channel_access import ChannelAccessObservation, ShadowChannelAccessAttempt
from ywd1278.tx.csma import CSMAParameters, CSMAState, DEFAULT_MAX_WAIT_SECONDS

from .control import TNCTransmitContext


RandomByteSource = Callable[[], int]


class ContextualFrameSubmitter(Protocol):
    """Downstream boundary that preserves the captured P6 request context."""

    def submit_frame(
        self,
        frame_with_fcs: bytes,
        context: TNCTransmitContext,
        *,
        timeout: float | None = None,
    ) -> object: ...


class KISSDataAdmissionError(RuntimeError):
    pass


class KISSDataFrameRejected(KISSDataAdmissionError):
    pass


class KISSDataQueueFull(KISSDataAdmissionError):
    pass


class KISSDataTimeError(KISSDataAdmissionError):
    pass


class KISSDataRequestState(str, Enum):
    QUEUED = "queued"
    ACCESS = "access"
    DISPATCHED = "dispatched"
    TIMED_OUT = "timed-out"
    DOWNSTREAM_FAILED = "downstream-failed"


@dataclass(frozen=True)
class KISSDataRequestReceipt:
    request_id: int
    frame_bytes_no_fcs: int
    frame_bytes_with_fcs: int
    parameter_generation: int
    txdelay: int
    persist: int
    slottime: int
    enqueued_at: float
    deadline_at: float


@dataclass(frozen=True)
class KISSDataQueueObservation:
    now: float
    request_id: int | None
    request_state: KISSDataRequestState | None
    parameter_generation: int | None
    access: ChannelAccessObservation | None
    downstream_called: bool
    downstream_result: object | None
    downstream_error: str
    reason: str


@dataclass(frozen=True)
class KISSDataQueueSnapshot:
    queue_depth: int
    queue_capacity: int
    active_request_id: int | None
    next_request_id: int
    accepted_requests: int
    invalid_rejections: int
    queue_full_rejections: int
    dispatched_requests: int
    timed_out_requests: int
    downstream_failures: int


@dataclass
class _Request:
    receipt: KISSDataRequestReceipt
    frame_with_fcs: bytes
    context: TNCTransmitContext
    attempt: ShadowChannelAccessAttempt | None = None


class KISSDataAdmissionQueue:
    """Bounded KISS DATA queue with immutable per-request P6 policy capture."""

    def __init__(
        self,
        submitter: ContextualFrameSubmitter,
        *,
        queue_capacity: int = 4,
        request_timeout_seconds: float = DEFAULT_MAX_WAIT_SECONDS,
        downstream_timeout_seconds: float = 1.5,
    ) -> None:
        if queue_capacity < 1:
            raise ValueError("queue_capacity must be at least 1")
        if request_timeout_seconds <= 0.0:
            raise ValueError("request_timeout_seconds must be positive")
        if downstream_timeout_seconds <= 0.0:
            raise ValueError("downstream_timeout_seconds must be positive")

        self._submitter = submitter
        self._queue_capacity = int(queue_capacity)
        self._request_timeout_seconds = float(request_timeout_seconds)
        self._downstream_timeout_seconds = float(downstream_timeout_seconds)
        self._queue: deque[_Request] = deque()
        self._next_request_id = 1
        self._last_now = 0.0
        self._accepted_requests = 0
        self._invalid_rejections = 0
        self._queue_full_rejections = 0
        self._dispatched_requests = 0
        self._timed_out_requests = 0
        self._downstream_failures = 0

    @property
    def request_timeout_seconds(self) -> float:
        return self._request_timeout_seconds

    @property
    def snapshot(self) -> KISSDataQueueSnapshot:
        active_id = None
        if self._queue and self._queue[0].attempt is not None:
            active_id = self._queue[0].receipt.request_id
        return KISSDataQueueSnapshot(
            queue_depth=len(self._queue),
            queue_capacity=self._queue_capacity,
            active_request_id=active_id,
            next_request_id=self._next_request_id,
            accepted_requests=self._accepted_requests,
            invalid_rejections=self._invalid_rejections,
            queue_full_rejections=self._queue_full_rejections,
            dispatched_requests=self._dispatched_requests,
            timed_out_requests=self._timed_out_requests,
            downstream_failures=self._downstream_failures,
        )

    def enqueue(
        self,
        frame_no_fcs: bytes,
        context: TNCTransmitContext,
        *,
        now: float,
    ) -> KISSDataRequestReceipt:
        """Validate one KISS AX.25 body, append FCS once, and admit it."""

        now = self._accept_now(now)
        frame_no_fcs = bytes(frame_no_fcs)
        try:
            parse_frame(frame_no_fcs, has_fcs=False)
        except (TypeError, ValueError) as exc:
            self._invalid_rejections += 1
            raise KISSDataFrameRejected(f"invalid AX.25 KISS DATA body: {exc}") from exc

        if len(self._queue) >= self._queue_capacity:
            self._queue_full_rejections += 1
            raise KISSDataQueueFull(
                f"KISS DATA queue is full (capacity={self._queue_capacity})"
            )

        frame_with_fcs = append_fcs(frame_no_fcs)
        receipt = KISSDataRequestReceipt(
            request_id=self._next_request_id,
            frame_bytes_no_fcs=len(frame_no_fcs),
            frame_bytes_with_fcs=len(frame_with_fcs),
            parameter_generation=context.parameters.generation,
            txdelay=context.parameters.txdelay,
            persist=context.parameters.persist,
            slottime=context.parameters.slottime,
            enqueued_at=now,
            deadline_at=now + self._request_timeout_seconds,
        )
        self._next_request_id += 1
        self._queue.append(
            _Request(
                receipt=receipt,
                frame_with_fcs=frame_with_fcs,
                context=context,
            )
        )
        self._accepted_requests += 1
        return receipt

    def observe_rssi(
        self,
        *,
        now: float,
        raw_magnitude: int,
        random_byte_source: RandomByteSource | None = None,
    ) -> KISSDataQueueObservation:
        """Advance only the head request using one raw-RSSI observation."""

        now = self._accept_now(now)
        if not self._queue:
            return KISSDataQueueObservation(
                now=now,
                request_id=None,
                request_state=None,
                parameter_generation=None,
                access=None,
                downstream_called=False,
                downstream_result=None,
                downstream_error="",
                reason="no queued KISS DATA request",
            )

        request = self._queue[0]
        receipt = request.receipt
        if now >= receipt.deadline_at:
            self._queue.popleft()
            self._timed_out_requests += 1
            return self._terminal(
                now,
                request,
                KISSDataRequestState.TIMED_OUT,
                access=None,
                reason="bounded total KISS DATA request lifetime expired before dispatch",
            )

        if request.attempt is None:
            remaining = receipt.deadline_at - now
            captured = request.context.csma_parameters
            parameters = CSMAParameters(
                persist=captured.persist,
                slot_time_10ms=captured.slot_time_10ms,
                max_wait_seconds=min(captured.max_wait_seconds, remaining),
            )
            request.attempt = ShadowChannelAccessAttempt(
                started_at=now,
                parameters=parameters,
            )

        access = request.attempt.observe_rssi(
            now=now,
            raw_magnitude=raw_magnitude,
            random_byte_source=random_byte_source,
        )

        if access.csma.state is CSMAState.TIMED_OUT:
            self._queue.popleft()
            self._timed_out_requests += 1
            return self._terminal(
                now,
                request,
                KISSDataRequestState.TIMED_OUT,
                access=access,
                reason="captured P6 channel-access policy timed out",
            )

        if access.csma.state is not CSMAState.READY:
            return KISSDataQueueObservation(
                now=now,
                request_id=receipt.request_id,
                request_state=KISSDataRequestState.ACCESS,
                parameter_generation=receipt.parameter_generation,
                access=access,
                downstream_called=False,
                downstream_result=None,
                downstream_error="",
                reason="KISS DATA request remains gated by qualified channel access",
            )

        # READY is consumed exactly once.  Remove the request immediately after
        # this synchronous submit attempt whether downstream succeeds or fails.
        try:
            result = self._submitter.submit_frame(
                request.frame_with_fcs,
                request.context,
                timeout=self._downstream_timeout_seconds,
            )
        except Exception as exc:
            self._queue.popleft()
            self._downstream_failures += 1
            return KISSDataQueueObservation(
                now=now,
                request_id=receipt.request_id,
                request_state=KISSDataRequestState.DOWNSTREAM_FAILED,
                parameter_generation=receipt.parameter_generation,
                access=access,
                downstream_called=True,
                downstream_result=None,
                downstream_error=f"{type(exc).__name__}: {exc}",
                reason="contextual downstream failed; request is terminal and is not retried",
            )

        self._queue.popleft()
        self._dispatched_requests += 1
        return KISSDataQueueObservation(
            now=now,
            request_id=receipt.request_id,
            request_state=KISSDataRequestState.DISPATCHED,
            parameter_generation=receipt.parameter_generation,
            access=access,
            downstream_called=True,
            downstream_result=result,
            downstream_error="",
            reason="captured P6 channel access reached READY; request dispatched exactly once",
        )

    def _terminal(
        self,
        now: float,
        request: _Request,
        state: KISSDataRequestState,
        *,
        access: ChannelAccessObservation | None,
        reason: str,
    ) -> KISSDataQueueObservation:
        return KISSDataQueueObservation(
            now=now,
            request_id=request.receipt.request_id,
            request_state=state,
            parameter_generation=request.receipt.parameter_generation,
            access=access,
            downstream_called=False,
            downstream_result=None,
            downstream_error="",
            reason=reason,
        )

    def _accept_now(self, now: float) -> float:
        now = float(now)
        if now < 0.0:
            raise KISSDataTimeError("now must be >= 0")
        if now < self._last_now:
            raise KISSDataTimeError("now must be monotonic for one P7 queue instance")
        self._last_now = now
        return now
