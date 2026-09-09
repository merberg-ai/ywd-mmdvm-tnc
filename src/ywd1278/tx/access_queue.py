"""Pure bounded TX-request scheduler above qualified 0C-P3 channel access.

0C-P4a connects a queued FCS-valid AX.25 request to the *decision* produced by
0C-P3, but deliberately does not import or construct the real TX broker.  A
caller injects a tiny ``FrameSubmitter`` interface; CI uses fake submitters only.

The scheduler is synchronous and deterministic:

* callers supply explicit monotonic time, RSSI observations, and persistence
  randomness;
* the request queue is bounded and fails closed when full;
* every accepted request has a fixed total lifetime from enqueue time;
* a fresh 0C-P3 access attempt is created only when a request reaches the head;
* no downstream submission occurs before CSMA reaches READY;
* READY can dispatch a request exactly once;
* timeout or downstream failure is terminal for that request;
* the next queued request begins only on a later caller observation.

There is no modem, UART, serial, RF, KISS, socket, thread, hidden clock, sleep,
or RNG dependency in this module.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Protocol

from ywd1278.ax25 import verify_fcs

from .channel_access import ChannelAccessObservation, ShadowChannelAccessAttempt
from .csma import (
    DEFAULT_MAX_WAIT_SECONDS,
    DEFAULT_PERSIST,
    DEFAULT_SLOT_TIME_10MS,
    CSMAParameters,
    CSMAState,
)


RandomByteSource = Callable[[], int]


class FrameSubmitter(Protocol):
    """Narrow downstream interface matching the qualified TX broker boundary."""

    def submit_frame(self, frame_with_fcs: bytes, *, timeout: float | None = None) -> object: ...


class AccessQueueError(RuntimeError):
    pass


class AccessQueueFull(AccessQueueError):
    pass


class AccessQueueFrameRejected(AccessQueueError):
    pass


class AccessQueueTimeError(AccessQueueError):
    pass


class AccessRequestState(str, Enum):
    QUEUED = "queued"
    ACCESS = "access"
    DISPATCHED = "dispatched"
    TIMED_OUT = "timed-out"
    DOWNSTREAM_FAILED = "downstream-failed"


@dataclass(frozen=True)
class AccessRequestReceipt:
    request_id: int
    frame_bytes: int
    enqueued_at: float
    deadline_at: float


@dataclass(frozen=True)
class AccessQueueObservation:
    now: float
    request_id: int | None
    request_state: AccessRequestState | None
    access: ChannelAccessObservation | None
    downstream_called: bool
    downstream_result: object | None
    downstream_error: str
    reason: str


@dataclass(frozen=True)
class AccessQueueSnapshot:
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
    receipt: AccessRequestReceipt
    frame: bytes
    attempt: ShadowChannelAccessAttempt | None = None


class BoundedChannelAccessQueue:
    """Deterministic bounded queue from valid AX.25 request to shadow READY.

    ``request_timeout_seconds`` is a total request lifetime measured from
    enqueue time.  Queue waiting therefore consumes the same finite budget as
    channel access.  When a request reaches the head, its P1 attempt receives
    only the remaining budget, capped by the already-qualified P1 maximum.

    The scheduler never retries a downstream submission.  A downstream error is
    terminal and the next queued request may begin only on a later observation.
    """

    def __init__(
        self,
        submitter: FrameSubmitter,
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
    def snapshot(self) -> AccessQueueSnapshot:
        active_id = None
        if self._queue and self._queue[0].attempt is not None:
            active_id = self._queue[0].receipt.request_id
        return AccessQueueSnapshot(
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

    def enqueue(self, frame_with_fcs: bytes, *, now: float) -> AccessRequestReceipt:
        """Validate and enqueue one complete FCS-bearing AX.25 frame."""

        now = self._accept_now(now)
        frame = bytes(frame_with_fcs)
        if len(frame) < 3 or not verify_fcs(frame):
            self._invalid_rejections += 1
            raise AccessQueueFrameRejected("AX.25 frame must include a valid FCS")
        if len(self._queue) >= self._queue_capacity:
            self._queue_full_rejections += 1
            raise AccessQueueFull(
                f"channel-access queue is full (capacity={self._queue_capacity})"
            )

        receipt = AccessRequestReceipt(
            request_id=self._next_request_id,
            frame_bytes=len(frame),
            enqueued_at=now,
            deadline_at=now + self._request_timeout_seconds,
        )
        self._next_request_id += 1
        self._queue.append(_Request(receipt=receipt, frame=frame))
        self._accepted_requests += 1
        return receipt

    def observe_rssi(
        self,
        *,
        now: float,
        raw_magnitude: int,
        random_byte_source: RandomByteSource | None = None,
    ) -> AccessQueueObservation:
        """Advance only the current head request using one RSSI observation."""

        now = self._accept_now(now)
        if not self._queue:
            return AccessQueueObservation(
                now=now,
                request_id=None,
                request_state=None,
                access=None,
                downstream_called=False,
                downstream_result=None,
                downstream_error="",
                reason="no queued transmit request",
            )

        request = self._queue[0]
        if now >= request.receipt.deadline_at:
            self._queue.popleft()
            self._timed_out_requests += 1
            return AccessQueueObservation(
                now=now,
                request_id=request.receipt.request_id,
                request_state=AccessRequestState.TIMED_OUT,
                access=None,
                downstream_called=False,
                downstream_result=None,
                downstream_error="",
                reason="bounded total request lifetime expired before dispatch",
            )

        if request.attempt is None:
            remaining = request.receipt.deadline_at - now
            parameters = CSMAParameters(
                persist=DEFAULT_PERSIST,
                slot_time_10ms=DEFAULT_SLOT_TIME_10MS,
                max_wait_seconds=min(DEFAULT_MAX_WAIT_SECONDS, remaining),
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
            return AccessQueueObservation(
                now=now,
                request_id=request.receipt.request_id,
                request_state=AccessRequestState.TIMED_OUT,
                access=access,
                downstream_called=False,
                downstream_result=None,
                downstream_error="",
                reason="qualified P1 channel-access attempt timed out",
            )

        if access.csma.state is not CSMAState.READY:
            return AccessQueueObservation(
                now=now,
                request_id=request.receipt.request_id,
                request_state=AccessRequestState.ACCESS,
                access=access,
                downstream_called=False,
                downstream_result=None,
                downstream_error="",
                reason="request remains gated by qualified channel access",
            )

        # READY is consumed exactly once because the request is removed from the
        # queue immediately after this one synchronous submit attempt, whether
        # the downstream accepts it or fails.
        try:
            result = self._submitter.submit_frame(
                request.frame,
                timeout=self._downstream_timeout_seconds,
            )
        except Exception as exc:
            self._queue.popleft()
            self._downstream_failures += 1
            return AccessQueueObservation(
                now=now,
                request_id=request.receipt.request_id,
                request_state=AccessRequestState.DOWNSTREAM_FAILED,
                access=access,
                downstream_called=True,
                downstream_result=None,
                downstream_error=f"{type(exc).__name__}: {exc}",
                reason="downstream submitter failed; request is terminal and is not retried",
            )

        self._queue.popleft()
        self._dispatched_requests += 1
        return AccessQueueObservation(
            now=now,
            request_id=request.receipt.request_id,
            request_state=AccessRequestState.DISPATCHED,
            access=access,
            downstream_called=True,
            downstream_result=result,
            downstream_error="",
            reason="qualified channel access reached READY; request dispatched exactly once",
        )

    def _accept_now(self, now: float) -> float:
        now = float(now)
        if now < 0.0:
            raise AccessQueueTimeError("now must be >= 0")
        if now < self._last_now:
            raise AccessQueueTimeError("now must be monotonic for one scheduler instance")
        self._last_now = now
        return now
