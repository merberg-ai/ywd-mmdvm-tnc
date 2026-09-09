"""Sustained bidirectional KISS TNC scheduler for YWD-1278 0C-P8.

This host/runtime layer deliberately reuses the physically-qualified P7 graph.
It owns no modem transport and performs no radio configuration.  The caller
must provide one already-running, packet-RX-active TXModemOwner and the same
thread-safe P8 admission queue used by the KISS backend.

One worker serially drains packet RX, checks FIFO health, polls RSSI only while
DATA is queued, advances the captured P2/P1 policy, and lets the unchanged P7
contextual submitter perform RX_STOP -> TX -> RF-idle -> RX_START.  Before any
queued request may advance channel access, the worker performs a bounded RX
backlog drain through the Bell-202 decoder.  The drain preserves priority for
already-captured bytes without chasing the continuously-producing 19.2 ksps
hardware sampler forever.  Bell-202 RX state is reset after every completed
half-duplex TX discontinuity.

Time and persistence randomness are explicit caller dependencies.  There is no
hidden RNG, POSIX serial, GPIO, flash, raw modem transaction, or direct TX call.
"""

from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Callable

from ywd1278.ax25 import parse_frame
from ywd1278.kiss.control import TNCControlCounters, TNCParameterSnapshot, TNCQueueAccounting
from ywd1278.kiss.server import PacketEvent
from ywd1278.kiss.sustained import (
    KISSConnectionCounters,
    SustainedTNCBackend,
    ThreadSafeKISSDataAdmissionQueue,
)
from ywd1278.kiss.tx_backend import KISSDataIngressCounters
from ywd1278.kiss.tx_path import KISSDataRequestState
from ywd1278.modem.tx_owner import TXModemOwner
from ywd1278.phy.bell202_rx import StreamingBell202Decoder, StreamingFrame


ACTIVE_RX_FLAGS = 0x0D
# AX25R4 has a 512-byte packed RX FIFO and the qualified host read maximum is
# 200 bytes.  Four reads can consume 800 bytes, comfortably more than one full
# hardware FIFO snapshot, while remaining bounded when the 19.2 ksps sampler
# continues producing new bytes during the UART transactions.  This mirrors
# the physically-qualified P4e/P7 live drain discipline.
RX_FIFO_DRAIN_READ_LIMIT = 4
MonotonicClock = Callable[[], float]
RandomByteSource = Callable[[], int]


class SustainedTNCRuntimeError(RuntimeError):
    pass


@dataclass(frozen=True)
class SustainedRuntimeCounters:
    running: bool
    identity: str
    rx_read_transactions: int
    packed_rx_bytes: int
    decoded_rx_frames: int
    rx_status_checks: int
    rssi_samples: int
    tx_dispatches: int
    access_timeouts: int
    decoder_resets_after_tx: int
    failure: str


@dataclass(frozen=True)
class SustainedTNCAccounting:
    runtime: SustainedRuntimeCounters
    parameters: TNCParameterSnapshot
    control: TNCControlCounters
    ingress: KISSDataIngressCounters
    queue: TNCQueueAccounting
    connections: KISSConnectionCounters
    subscriber_drops: int


class SustainedTNCRuntime:
    """Single-worker sustained scheduler over the qualified P7/P4e/P5 graph."""

    def __init__(
        self,
        owner: TXModemOwner,
        backend: SustainedTNCBackend,
        admission: ThreadSafeKISSDataAdmissionQueue,
        *,
        expected_identity: str,
        monotonic: MonotonicClock,
        random_byte_source: RandomByteSource,
        read_maximum: int = 200,
        idle_sleep_seconds: float = 0.005,
        status_interval_seconds: float = 0.25,
        thread_name: str = "ywd1278-p8-sustained-tnc",
    ) -> None:
        if not expected_identity.strip():
            raise ValueError("expected_identity must be non-empty")
        if not 1 <= int(read_maximum) <= 200:
            raise ValueError("read_maximum must be 1..200")
        if idle_sleep_seconds < 0.0:
            raise ValueError("idle_sleep_seconds must be >= 0")
        if status_interval_seconds <= 0.0:
            raise ValueError("status_interval_seconds must be positive")
        if not callable(monotonic):
            raise TypeError("monotonic must be caller supplied")
        if not callable(random_byte_source):
            raise TypeError("random_byte_source must be caller supplied")

        self._owner = owner
        self._backend = backend
        self._admission = admission
        self._expected_identity = expected_identity
        self._monotonic = monotonic
        self._random_byte_source = random_byte_source
        self._read_maximum = int(read_maximum)
        self._idle_sleep_seconds = float(idle_sleep_seconds)
        self._status_interval_seconds = float(status_interval_seconds)
        self._thread_name = thread_name

        self._decoder = StreamingBell202Decoder()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._started = False
        self._identity = ""
        self._rx_reads = 0
        self._packed_bytes = 0
        self._decoded_frames = 0
        self._status_checks = 0
        self._rssi_samples = 0
        self._tx_dispatches = 0
        self._access_timeouts = 0
        self._decoder_resets = 0
        self._failure: BaseException | None = None

    @property
    def runtime_counters(self) -> SustainedRuntimeCounters:
        with self._lock:
            thread = self._thread
            return SustainedRuntimeCounters(
                running=bool(thread and thread.is_alive() and not self._stop.is_set()),
                identity=self._identity,
                rx_read_transactions=self._rx_reads,
                packed_rx_bytes=self._packed_bytes,
                decoded_rx_frames=self._decoded_frames,
                rx_status_checks=self._status_checks,
                rssi_samples=self._rssi_samples,
                tx_dispatches=self._tx_dispatches,
                access_timeouts=self._access_timeouts,
                decoder_resets_after_tx=self._decoder_resets,
                failure="" if self._failure is None else f"{type(self._failure).__name__}: {self._failure}",
            )

    @property
    def accounting(self) -> SustainedTNCAccounting:
        base = self._backend.snapshot
        return SustainedTNCAccounting(
            runtime=self.runtime_counters,
            parameters=self._backend.control_snapshot,
            control=self._backend.control_counters,
            ingress=self._backend.ingress_counters,
            queue=TNCQueueAccounting.from_access_snapshot(self._admission.snapshot),
            connections=self._backend.connection_counters,
            subscriber_drops=base.subscriber_drops,
        )

    def start(self, *, timeout: float = 1.5) -> None:
        if timeout <= 0.0:
            raise ValueError("timeout must be positive")
        with self._lock:
            if self._started:
                raise SustainedTNCRuntimeError("sustained TNC runtime cannot be restarted")
            self._started = True

        # P8 does not construct/start/configure the owner.  It only verifies the
        # caller-provided qualified owner and active packet-RX state.
        version = self._owner.get_version(timeout=timeout)
        if version.identity != self._expected_identity:
            raise SustainedTNCRuntimeError(
                "packet firmware identity mismatch: "
                f"expected={self._expected_identity!r} actual={version.identity!r}"
            )
        status = self._owner.rx_status(timeout=timeout)
        self._record_status_check()
        self._require_active_status(status)
        with self._lock:
            self._identity = version.identity

        self._stop.clear()
        thread = threading.Thread(target=self._run, name=self._thread_name, daemon=True)
        with self._lock:
            self._thread = thread
        thread.start()

    def stop(self, *, timeout: float = 3.0) -> None:
        if timeout <= 0.0:
            raise ValueError("timeout must be positive")
        self._stop.set()
        with self._lock:
            thread = self._thread
        if thread is not None:
            thread.join(timeout)
            if thread.is_alive():
                raise SustainedTNCRuntimeError("timed out waiting for sustained TNC worker")
        self.check_health()

    def check_health(self) -> None:
        with self._lock:
            failure = self._failure
        if failure is not None:
            raise SustainedTNCRuntimeError("sustained TNC runtime failed") from failure

    def _run(self) -> None:
        next_status = float(self._monotonic()) + self._status_interval_seconds
        try:
            while not self._stop.is_set():
                # Preserve RX backlog priority, but do not chase the live raw
                # sampler indefinitely.  A bounded pass consumes more than one
                # complete 512-byte hardware FIFO snapshot and then gives RSSI/
                # CSMA a scheduling opportunity before RX is serviced again.
                self._drain_rx_fifo()

                now = float(self._monotonic())
                if now >= next_status:
                    status = self._owner.rx_status()
                    self._record_status_check()
                    self._require_active_status(status)
                    next_status = now + self._status_interval_seconds

                if self._admission.snapshot.queue_depth:
                    rssi = self._owner.rx_rssi()
                    with self._lock:
                        self._rssi_samples += 1
                    observation = self._admission.observe_rssi(
                        now=now,
                        raw_magnitude=rssi.raw_magnitude,
                        random_byte_source=self._random_byte_source,
                    )
                    if observation.request_state is KISSDataRequestState.TIMED_OUT:
                        with self._lock:
                            self._access_timeouts += 1
                    elif observation.request_state is KISSDataRequestState.DOWNSTREAM_FAILED:
                        raise SustainedTNCRuntimeError(
                            "P7 contextual downstream failed; request is terminal and service is fail-latched: "
                            f"{observation.downstream_error}"
                        )
                    elif (
                        observation.request_state is KISSDataRequestState.DISPATCHED
                        and observation.downstream_called
                    ):
                        # P4e returns only after RF idle and RX restart.  The RF
                        # gap invalidates streaming Bell-202 phase/history, so a
                        # fresh decoder is mandatory before consuming new RX.
                        self._decoder = StreamingBell202Decoder()
                        with self._lock:
                            self._tx_dispatches += 1
                            self._decoder_resets += 1
                        restarted = self._owner.rx_status()
                        self._record_status_check()
                        self._require_active_status(restarted)
                        next_status = float(self._monotonic()) + self._status_interval_seconds

                if self._idle_sleep_seconds and not self._stop.is_set():
                    self._stop.wait(self._idle_sleep_seconds)
        except BaseException as exc:
            with self._lock:
                self._failure = exc
            self._stop.set()

    def _drain_rx_fifo(self) -> None:
        """Drain pre-existing RX backlog without chasing continuous new samples.

        AX25R4's raw sampler keeps producing packed bytes while RX is active,
        including during RF silence.  Waiting for a zero-length RX_READ can
        therefore starve RSSI/CSMA forever on real hardware even though a finite
        fake buffer eventually returns empty.  Four 200-byte reads exceed the
        complete 512-byte firmware FIFO capacity, so a bounded four-read pass
        preserves backlog priority while guaranteeing scheduler progress.
        """

        for _ in range(RX_FIFO_DRAIN_READ_LIMIT):
            if self._stop.is_set():
                return
            chunk = self._owner.rx_read(self._read_maximum)
            with self._lock:
                self._rx_reads += 1
            if chunk:
                self._consume(chunk)
            # A partial read proves the backlog present at this instant was
            # overtaken; do not issue another read merely to wait for an exact
            # zero while the hardware sampler continues producing bytes.
            if len(chunk) < self._read_maximum:
                return

    def _consume(self, packed: bytes) -> None:
        fresh = self._decoder.feed(packed)
        with self._lock:
            self._packed_bytes += len(packed)
        for item in fresh:
            self._publish(item)

    def _publish(self, item: StreamingFrame) -> None:
        parsed = parse_frame(item.frame, has_fcs=True)
        self._backend.publish(
            PacketEvent(
                frame_no_fcs=item.frame[:-2],
                source=str(parsed["source"]),
                destination=str(parsed["destination"]),
                frame_type=str(parsed["frame_type"]),
            )
        )
        with self._lock:
            self._decoded_frames += 1

    def _record_status_check(self) -> None:
        with self._lock:
            self._status_checks += 1

    @staticmethod
    def _require_active_status(status) -> None:  # type: ignore[no-untyped-def]
        if status.flags != ACTIVE_RX_FLAGS:
            raise SustainedTNCRuntimeError(
                f"packet RX flags changed: expected=0x{ACTIVE_RX_FLAGS:02x} actual=0x{status.flags:02x}"
            )
        if status.dropped_bytes != 0:
            raise SustainedTNCRuntimeError(
                f"packet RX FIFO dropped {status.dropped_bytes} packed bytes"
            )
