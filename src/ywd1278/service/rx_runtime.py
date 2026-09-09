"""Assembled RX-only packet runtime for YWD-1278.

The runtime joins already-qualified layers without creating a transmit path:

single ModemOwner -> YWD_RX FIFO -> StreamingBell202Decoder -> PacketEvent ->
RXOnlyBackend -> TCP KISS (when a server is attached to the backend).

For live hardware, ``frequency_hz`` enables the exact RX-safe setup sequence
from the frozen AX25R3 engineering capture: guarded SET_FREQ, fixed idle
SET_CONFIG, read-only RF status verification, then YWD_RX/START. Replay tests
may leave ``frequency_hz`` unset because their injected transport already
represents a configured packet FIFO.

Only the ModemOwner can touch the transport. This module has no raw serial
access and no TX operation. RX FIFO drops are fatal and stop the receive
runtime rather than being silently reported as healthy packet service.
"""

from __future__ import annotations

from dataclasses import dataclass
import threading
import time

from ywd1278.ax25 import parse_frame
from ywd1278.kiss.server import PacketEvent, RXOnlyBackend
from ywd1278.modem.owner import ModemOwner
from ywd1278.phy.bell202_rx import StreamingBell202Decoder, StreamingFrame

ACTIVE_RX_FLAGS = 0x0D
IDLE_RX_FLAGS = 0x04
ARMED_IDLE_RF_FLAGS = 0x08


class RXRuntimeError(RuntimeError):
    """Receive-runtime lifecycle or health failure."""


@dataclass(frozen=True)
class RXRuntimeSnapshot:
    running: bool
    identity: str
    frequency_hz: int | None
    read_transactions: int
    packed_bytes: int
    decoded_frames: int
    status_checks: int
    firmware_samples: int
    fifo_available_bytes: int
    fifo_dropped_bytes: int
    failure: str


class RXOnlyPacketRuntime:
    """Bounded, TX-disconnected product receive pipeline.

    ``expected_identity`` is mandatory so a stock or otherwise unexpected
    firmware image cannot accidentally be treated as packet-capable.

    Set ``frequency_hz`` for real hardware. When present, startup reproduces
    the exact qualified MMDVM radio initialization before YWD_RX/START. Leaving
    it unset is intended only for injected/replay transports.
    """

    def __init__(
        self,
        owner: ModemOwner,
        backend: RXOnlyBackend,
        *,
        expected_identity: str,
        frequency_hz: int | None = None,
        read_maximum: int = 200,
        idle_sleep_seconds: float = 0.002,
        status_interval_seconds: float = 1.0,
        thread_name: str = "ywd1278-rx-runtime",
    ) -> None:
        if not expected_identity.strip():
            raise ValueError("expected_identity must be non-empty")
        if frequency_hz is not None and frequency_hz <= 0:
            raise ValueError("frequency_hz must be positive when provided")
        if not 1 <= read_maximum <= 200:
            raise ValueError("read_maximum must be 1..200")
        if idle_sleep_seconds < 0.0:
            raise ValueError("idle_sleep_seconds must be >= 0")
        if status_interval_seconds <= 0.0:
            raise ValueError("status_interval_seconds must be positive")

        self._owner = owner
        self._backend = backend
        self._expected_identity = expected_identity
        self._frequency_hz = None if frequency_hz is None else int(frequency_hz)
        self._read_maximum = int(read_maximum)
        self._idle_sleep_seconds = float(idle_sleep_seconds)
        self._status_interval_seconds = float(status_interval_seconds)
        self._thread_name = thread_name

        self._decoder = StreamingBell202Decoder()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._identity = ""
        self._read_transactions = 0
        self._packed_bytes = 0
        self._decoded_frames = 0
        self._status_checks = 0
        self._firmware_samples = 0
        self._fifo_available = 0
        self._fifo_dropped = 0
        self._failure: BaseException | None = None
        self._rx_started = False

    @property
    def snapshot(self) -> RXRuntimeSnapshot:
        with self._lock:
            thread = self._thread
            return RXRuntimeSnapshot(
                running=bool(thread and thread.is_alive() and not self._stop.is_set()),
                identity=self._identity,
                frequency_hz=self._frequency_hz,
                read_transactions=self._read_transactions,
                packed_bytes=self._packed_bytes,
                decoded_frames=self._decoded_frames,
                status_checks=self._status_checks,
                firmware_samples=self._firmware_samples,
                fifo_available_bytes=self._fifo_available,
                fifo_dropped_bytes=self._fifo_dropped,
                failure="" if self._failure is None else str(self._failure),
            )

    def start(self, *, timeout: float = 2.0) -> None:
        with self._lock:
            if self._thread is not None:
                raise RXRuntimeError("RX runtime cannot be restarted")

        self._owner.start(timeout=timeout)
        try:
            version = self._owner.get_version(timeout=timeout)
            if version.identity != self._expected_identity:
                raise RXRuntimeError(
                    "packet firmware identity mismatch: "
                    f"expected={self._expected_identity!r} actual={version.identity!r}"
                )
            with self._lock:
                self._identity = version.identity

            if self._frequency_hz is not None:
                self._owner.set_rx_frequency(self._frequency_hz, timeout=timeout)
                self._owner.arm_rx_modem_io(timeout=timeout)
                rf = self._owner.rf_status(timeout=timeout)
                if rf.flags != ARMED_IDLE_RF_FLAGS or rf.remaining_selectors != 0 or rf.mode != 0:
                    raise RXRuntimeError(
                        "modem IO did not enter armed idle before packet RX: "
                        f"flags=0x{rf.flags:02x} remaining={rf.remaining_selectors} mode={rf.mode}"
                    )

            self._owner.rx_start(timeout=timeout)
            self._rx_started = True
            status = self._owner.rx_status(timeout=timeout)
            self._record_status(status)
            self._require_active_status(status)

            self._stop.clear()
            thread = threading.Thread(target=self._run, name=self._thread_name, daemon=True)
            with self._lock:
                self._thread = thread
            thread.start()
        except BaseException:
            self._safe_quiesce(timeout=timeout)
            raise

    def stop(self, *, timeout: float = 3.0) -> None:
        self._stop.set()
        with self._lock:
            thread = self._thread
        if thread is not None:
            thread.join(timeout)
            if thread.is_alive():
                raise RXRuntimeError("timed out waiting for RX runtime worker")

        cleanup_error: BaseException | None = None
        try:
            self._stop_and_drain(timeout=timeout)
        except BaseException as exc:
            cleanup_error = exc
        finally:
            try:
                self._owner.stop(timeout=timeout)
            except BaseException as exc:
                if cleanup_error is None:
                    cleanup_error = exc

        with self._lock:
            worker_error = self._failure
        if worker_error is not None:
            raise RXRuntimeError("RX runtime failed") from worker_error
        if cleanup_error is not None:
            raise RXRuntimeError("RX runtime cleanup failed") from cleanup_error

    def check_health(self) -> None:
        with self._lock:
            failure = self._failure
        if failure is not None:
            raise RXRuntimeError("RX runtime failed") from failure

    def _run(self) -> None:
        next_status = time.monotonic() + self._status_interval_seconds
        try:
            while not self._stop.is_set():
                chunk = self._owner.rx_read(self._read_maximum)
                with self._lock:
                    self._read_transactions += 1
                if chunk:
                    self._consume(chunk)
                elif self._idle_sleep_seconds:
                    self._stop.wait(self._idle_sleep_seconds)

                now = time.monotonic()
                if now >= next_status:
                    status = self._owner.rx_status()
                    self._record_status(status)
                    self._require_active_status(status)
                    next_status = now + self._status_interval_seconds
        except BaseException as exc:
            with self._lock:
                self._failure = exc
            self._stop.set()
            self._safe_quiesce(timeout=1.0)

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

    def _record_status(self, status) -> None:
        with self._lock:
            self._status_checks += 1
            self._firmware_samples = status.samples
            self._fifo_available = status.available_bytes
            self._fifo_dropped = status.dropped_bytes

    @staticmethod
    def _require_active_status(status) -> None:
        if status.flags != ACTIVE_RX_FLAGS:
            raise RXRuntimeError(
                f"packet RX flags changed: expected=0x{ACTIVE_RX_FLAGS:02x} "
                f"actual=0x{status.flags:02x}"
            )
        if status.dropped_bytes != 0:
            raise RXRuntimeError(f"packet RX FIFO dropped {status.dropped_bytes} packed bytes")

    def _stop_and_drain(self, *, timeout: float) -> None:
        if not self._rx_started:
            return

        self._owner.rx_stop(timeout=timeout)
        self._rx_started = False

        while True:
            chunk = self._owner.rx_read(self._read_maximum, timeout=timeout)
            with self._lock:
                self._read_transactions += 1
            if not chunk:
                break
            self._consume(chunk)
        self._decoder.finish()

        status = self._owner.rx_status(timeout=timeout)
        self._record_status(status)
        if status.flags != IDLE_RX_FLAGS:
            raise RXRuntimeError(
                f"packet RX did not return to armed idle: flags=0x{status.flags:02x}"
            )
        if status.available_bytes != 0:
            raise RXRuntimeError(
                f"packet RX FIFO not empty after stop: {status.available_bytes} bytes"
            )
        if status.dropped_bytes != 0:
            raise RXRuntimeError(
                f"packet RX FIFO dropped {status.dropped_bytes} packed bytes"
            )

    def _safe_quiesce(self, *, timeout: float) -> None:
        if self._rx_started:
            try:
                self._owner.rx_stop(timeout=timeout)
            except BaseException:
                pass
            self._rx_started = False
        try:
            self._owner.stop(timeout=timeout)
        except BaseException:
            pass
