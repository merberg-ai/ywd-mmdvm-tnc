"""Bounded, fail-closed Bell-202 transmit broker.

0B-P13a deliberately stops short of KISS-originated or autonomous RF TX.  The
broker is a host-side scheduling boundary above the single modem owner.  It
accepts only complete AX.25 frames with valid FCS, reuses the frozen 0B-P5
serializer with fixed timing, and forwards exactly one typed selector burst to
a TX-capable modem owner.

The broker defaults to transmit-disabled and has no runtime enable toggle.  A
caller must construct a new instance with ``transmit_enabled=True`` for an
explicitly guarded qualification.  Channel sensing/CSMA, TXDELAY configuration,
KISS parameter commands, and persistent bidirectional service behavior belong
to later phases.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import queue
import threading
from typing import Protocol, cast

from ywd1278.ax25 import verify_fcs
from ywd1278.modem import protocol
from ywd1278.phy import MARK, duration_seconds, frame_to_selectors, pack_selectors

P5_PRE_FLAGS = 45
P5_POST_FLAGS = 3
P5_INITIAL_TONE = MARK


class TXModemPort(Protocol):
    """Only the modem operations reachable from the TX broker."""

    def rf_status(self, *, timeout: float | None = None) -> protocol.RFStatus: ...

    def transmit_selector_burst(
        self,
        selector_count: int,
        packed_selectors: bytes,
        *,
        timeout: float | None = None,
    ) -> None: ...


class TXBrokerError(RuntimeError):
    pass


class TXBrokerDisabled(TXBrokerError):
    pass


class TXBrokerNotRunning(TXBrokerError):
    pass


class TXBrokerQueueFull(TXBrokerError):
    pass


class TXBrokerFrameRejected(TXBrokerError):
    pass


class TXBrokerBusy(TXBrokerError):
    pass


@dataclass(frozen=True)
class TXReceipt:
    frame_bytes: int
    frame_sha256: str
    selector_count: int
    packed_selector_bytes: int
    packed_selector_sha256: str
    nominal_duration_seconds: float


@dataclass(frozen=True)
class TXBrokerSnapshot:
    running: bool
    transmit_enabled: bool
    queue_depth: int
    queue_capacity: int
    in_flight: bool
    submitted: int
    accepted: int
    failed: int
    invalid_rejections: int
    queue_full_rejections: int
    busy_rejections: int


@dataclass
class _Job:
    receipt: TXReceipt
    packed_selectors: bytes
    transaction_timeout: float
    done: threading.Event
    error: BaseException | None = None


_STOP = object()


class TXBroker:
    """One-worker bounded TX queue above a single modem owner.

    ``submit_frame`` is synchronous from the caller's perspective, but all
    modem-facing work occurs in the broker worker and then through the modem
    owner's own single-UART thread.  Queue capacity is finite and queue-full
    submission fails closed.

    The broker performs a read-only RF-status preflight immediately before each
    selector burst.  If the modem reports selectors already pending, the new
    burst is rejected rather than overlapped.  This is *not* CSMA/channel-busy
    detection; over-air channel access remains a later qualification.
    """

    def __init__(
        self,
        owner: TXModemPort,
        *,
        transmit_enabled: bool = False,
        queue_capacity: int = 4,
        submit_timeout: float = 0.05,
        default_transaction_timeout: float = 1.5,
        thread_name: str = "ywd1278-tx-broker",
    ) -> None:
        if queue_capacity < 1:
            raise ValueError("queue_capacity must be at least 1")
        if submit_timeout <= 0.0:
            raise ValueError("submit_timeout must be positive")
        if default_transaction_timeout <= 0.0:
            raise ValueError("default_transaction_timeout must be positive")

        self._owner = owner
        self._transmit_enabled = bool(transmit_enabled)
        self._queue: queue.Queue[_Job | object] = queue.Queue(maxsize=queue_capacity)
        self._submit_timeout = float(submit_timeout)
        self._default_transaction_timeout = float(default_transaction_timeout)
        self._thread_name = thread_name

        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._accepting = False
        self._in_flight = False
        self._submitted = 0
        self._accepted = 0
        self._failed = 0
        self._invalid_rejections = 0
        self._queue_full_rejections = 0
        self._busy_rejections = 0

    def start(self) -> None:
        with self._lock:
            if self._thread is not None:
                if self._thread.is_alive() and self._accepting:
                    return
                raise TXBrokerError("TX broker cannot be restarted")
            self._accepting = True
            self._thread = threading.Thread(
                target=self._run,
                name=self._thread_name,
                daemon=True,
            )
            self._thread.start()

    def stop(self, *, timeout: float = 2.0) -> None:
        if timeout <= 0.0:
            raise ValueError("timeout must be positive")
        with self._lock:
            thread = self._thread
            if thread is None or not thread.is_alive():
                self._accepting = False
                return
            self._accepting = False

        try:
            self._queue.put(_STOP, timeout=timeout)
        except queue.Full as exc:
            raise TXBrokerError("timed out queueing TX-broker stop") from exc
        thread.join(timeout)
        if thread.is_alive():
            raise TXBrokerError("timed out waiting for TX-broker shutdown")

    def __enter__(self) -> "TXBroker":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        self.stop()

    @property
    def snapshot(self) -> TXBrokerSnapshot:
        with self._lock:
            thread = self._thread
            running = bool(thread and thread.is_alive() and self._accepting)
            return TXBrokerSnapshot(
                running=running,
                transmit_enabled=self._transmit_enabled,
                queue_depth=self._queue.qsize(),
                queue_capacity=self._queue.maxsize,
                in_flight=self._in_flight,
                submitted=self._submitted,
                accepted=self._accepted,
                failed=self._failed,
                invalid_rejections=self._invalid_rejections,
                queue_full_rejections=self._queue_full_rejections,
                busy_rejections=self._busy_rejections,
            )

    def submit_frame(
        self,
        frame_with_fcs: bytes,
        *,
        timeout: float | None = None,
    ) -> TXReceipt:
        """Validate, serialize, queue, and submit one FCS-bearing AX.25 frame."""

        if not self._transmit_enabled:
            raise TXBrokerDisabled("TX broker was constructed transmit-disabled")

        transaction_timeout = (
            self._default_transaction_timeout if timeout is None else float(timeout)
        )
        if transaction_timeout <= 0.0:
            raise ValueError("transaction timeout must be positive")

        with self._lock:
            thread = self._thread
            accepting = self._accepting
        if thread is None or not thread.is_alive() or not accepting:
            raise TXBrokerNotRunning("TX broker is not accepting submissions")

        try:
            receipt, packed = self._prepare_frame(bytes(frame_with_fcs))
        except TXBrokerFrameRejected:
            with self._lock:
                self._invalid_rejections += 1
            raise

        job = _Job(
            receipt=receipt,
            packed_selectors=packed,
            transaction_timeout=transaction_timeout,
            done=threading.Event(),
        )
        try:
            self._queue.put(job, timeout=self._submit_timeout)
        except queue.Full as exc:
            with self._lock:
                self._queue_full_rejections += 1
            raise TXBrokerQueueFull(
                f"TX broker queue is full (capacity={self._queue.maxsize})"
            ) from exc

        with self._lock:
            self._submitted += 1

        # The worker performs one read-only status transaction and one TX
        # transaction.  Give both their full transaction timeout plus bounded
        # queueing/scheduling margin.
        wait_timeout = (2.0 * transaction_timeout) + self._submit_timeout + 1.0
        if not job.done.wait(wait_timeout):
            raise TXBrokerError("timed out waiting for TX broker operation")
        if job.error is not None:
            if isinstance(job.error, TXBrokerError):
                raise job.error
            raise TXBrokerError("TX broker operation failed") from job.error
        return receipt

    def _prepare_frame(self, frame: bytes) -> tuple[TXReceipt, bytes]:
        if len(frame) < 3 or not verify_fcs(frame):
            raise TXBrokerFrameRejected("AX.25 frame must include a valid FCS")

        selectors = frame_to_selectors(
            frame,
            pre_flags=P5_PRE_FLAGS,
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

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is _STOP:
                    return
                job = cast(_Job, item)

                with self._lock:
                    accepting = self._accepting
                if not accepting:
                    job.error = TXBrokerNotRunning(
                        "TX broker stopped before queued transmission began"
                    )
                    with self._lock:
                        self._failed += 1
                    job.done.set()
                    continue

                with self._lock:
                    self._in_flight = True
                try:
                    status = self._owner.rf_status(timeout=job.transaction_timeout)
                    if status.remaining_selectors != 0:
                        with self._lock:
                            self._busy_rejections += 1
                        raise TXBrokerBusy(
                            "modem already has pending TX selectors; refusing overlap"
                        )
                    self._owner.transmit_selector_burst(
                        job.receipt.selector_count,
                        job.packed_selectors,
                        timeout=job.transaction_timeout,
                    )
                    with self._lock:
                        self._accepted += 1
                except BaseException as exc:
                    job.error = exc
                    with self._lock:
                        self._failed += 1
                finally:
                    with self._lock:
                        self._in_flight = False
                    job.done.set()
            finally:
                self._queue.task_done()
