"""Bounded single-owner modem command runtime.

Public clients receive typed operations only. The transport object is created,
used, and closed inside exactly one owner thread, so device ownership is a
structural boundary rather than a convention.

The receive path can perform the exact normal MMDVM setup used by the frozen
AX25R3 capture lineage. 0C-P2 adds one read-only typed RSSI operation for the
staged AX25R4 firmware; callers still cannot submit arbitrary configuration
bytes and there is no TX_TONES method in this base layer.
"""

from __future__ import annotations

from dataclasses import dataclass
import queue
import threading
from typing import Callable, Protocol, TypeVar, cast

from . import protocol, rx_config


class ModemTransport(Protocol):
    """Minimal transport contract consumed only by :class:`ModemOwner`."""

    def transact(self, request: bytes, *, timeout: float) -> bytes: ...

    def close(self) -> None: ...


TransportFactory = Callable[[], ModemTransport]
T = TypeVar("T")


class ModemOwnerError(RuntimeError):
    pass


class ModemOwnerNotRunning(ModemOwnerError):
    pass


class ModemOwnerQueueFull(ModemOwnerError):
    pass


@dataclass(frozen=True)
class OwnerSnapshot:
    running: bool
    owner_thread_id: int | None
    queue_depth: int
    queue_capacity: int
    transactions: int


@dataclass
class _Call:
    operation: str
    argument: int | None
    timeout: float
    done: threading.Event
    result: object | None = None
    error: BaseException | None = None


_STOP = object()


class ModemOwner:
    """Exactly-one-thread modem transaction owner with a bounded request queue.

    Public callers receive typed methods only. They cannot pass a raw modem
    frame through this object. The reachable RX/control command set is:

    * GET_VERSION
    * guarded simplex SET_FREQ for receive setup
    * fixed RX-safe SET_CONFIG modem-I/O initialization
    * YWD_RX START / READ / STATUS / STOP
    * YWD_RX RSSI read-only telemetry on AX25R4 firmware
    * YWD_RF GET_STATUS / GET_DIAG read-only diagnostics

    There is intentionally no RF TX/ABORT/EXIT API here. The narrow
    :class:`TXModemOwner` subclass remains the only owner with the qualified
    selector-burst TX primitive.
    """

    def __init__(
        self,
        transport_factory: TransportFactory,
        *,
        queue_capacity: int = 8,
        submit_timeout: float = 0.25,
        default_transaction_timeout: float = 1.0,
        thread_name: str = "ywd1278-modem-owner",
    ) -> None:
        if queue_capacity < 1:
            raise ValueError("queue_capacity must be at least 1")
        if submit_timeout <= 0.0:
            raise ValueError("submit_timeout must be positive")
        if default_transaction_timeout <= 0.0:
            raise ValueError("default_transaction_timeout must be positive")

        self._transport_factory = transport_factory
        self._queue: queue.Queue[_Call | object] = queue.Queue(maxsize=queue_capacity)
        self._submit_timeout = submit_timeout
        self._default_transaction_timeout = default_transaction_timeout
        self._thread_name = thread_name

        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._stopped = threading.Event()
        self._start_error: BaseException | None = None
        self._owner_thread_id: int | None = None
        self._transactions = 0
        self._accepting = False

    def start(self, *, timeout: float = 2.0) -> None:
        if timeout <= 0.0:
            raise ValueError("timeout must be positive")
        with self._lock:
            if self._thread is not None:
                if self._thread.is_alive() and self._accepting:
                    return
                raise ModemOwnerError("modem owner cannot be restarted")
            self._ready.clear()
            self._stopped.clear()
            self._start_error = None
            self._thread = threading.Thread(
                target=self._run,
                name=self._thread_name,
                daemon=True,
            )
            self._thread.start()

        if not self._ready.wait(timeout):
            raise ModemOwnerError("timed out waiting for modem owner startup")
        if self._start_error is not None:
            raise ModemOwnerError("modem transport startup failed") from self._start_error

    def stop(self, *, timeout: float = 2.0) -> None:
        if timeout <= 0.0:
            raise ValueError("timeout must be positive")
        with self._lock:
            thread = self._thread
            if thread is None:
                return
            if not thread.is_alive():
                return
            self._accepting = False

        try:
            self._queue.put(_STOP, timeout=timeout)
        except queue.Full as exc:
            raise ModemOwnerError("timed out queueing modem-owner stop") from exc
        thread.join(timeout)
        if thread.is_alive():
            raise ModemOwnerError("timed out waiting for modem owner shutdown")

    def __enter__(self) -> "ModemOwner":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        self.stop()

    @property
    def snapshot(self) -> OwnerSnapshot:
        with self._lock:
            thread = self._thread
            running = bool(thread and thread.is_alive() and self._accepting)
            owner_thread_id = self._owner_thread_id
            transactions = self._transactions
        return OwnerSnapshot(
            running=running,
            owner_thread_id=owner_thread_id,
            queue_depth=self._queue.qsize(),
            queue_capacity=self._queue.maxsize,
            transactions=transactions,
        )

    def get_version(self, *, timeout: float | None = None) -> protocol.VersionResponse:
        return cast(protocol.VersionResponse, self._call("get_version", None, timeout))

    def set_rx_frequency(self, frequency_hz: int, *, timeout: float | None = None) -> None:
        self._call("set_rx_frequency", int(frequency_hz), timeout)

    def arm_rx_modem_io(self, *, timeout: float | None = None) -> None:
        self._call("arm_rx_modem_io", None, timeout)

    def rx_start(self, *, timeout: float | None = None) -> None:
        self._call("rx_start", None, timeout)

    def rx_read(
        self,
        maximum: int = protocol.MAX_RX_READ_BYTES,
        *,
        timeout: float | None = None,
    ) -> bytes:
        return cast(bytes, self._call("rx_read", maximum, timeout))

    def rx_status(self, *, timeout: float | None = None) -> protocol.RX3Status:
        return cast(protocol.RX3Status, self._call("rx_status", None, timeout))

    def rx_rssi(self, *, timeout: float | None = None) -> protocol.RXRSSI:
        """Read one raw ADF7021 RSSI magnitude through the single owner thread."""
        return cast(protocol.RXRSSI, self._call("rx_rssi", None, timeout))

    def rx_stop(self, *, timeout: float | None = None) -> None:
        self._call("rx_stop", None, timeout)

    def rf_status(self, *, timeout: float | None = None) -> protocol.RFStatus:
        return cast(protocol.RFStatus, self._call("rf_status", None, timeout))

    def rf_diagnostics(self, *, timeout: float | None = None) -> protocol.RFDiagnostics:
        return cast(protocol.RFDiagnostics, self._call("rf_diag", None, timeout))

    def _call(self, operation: str, argument: int | None, timeout: float | None) -> object | None:
        transaction_timeout = (
            self._default_transaction_timeout if timeout is None else timeout
        )
        if transaction_timeout <= 0.0:
            raise ValueError("transaction timeout must be positive")

        with self._lock:
            thread = self._thread
            accepting = self._accepting
        if thread is None or not thread.is_alive() or not accepting:
            raise ModemOwnerNotRunning("modem owner is not accepting requests")

        call = _Call(
            operation=operation,
            argument=argument,
            timeout=transaction_timeout,
            done=threading.Event(),
        )
        try:
            self._queue.put(call, timeout=self._submit_timeout)
        except queue.Full as exc:
            raise ModemOwnerQueueFull(
                f"modem owner request queue is full (capacity={self._queue.maxsize})"
            ) from exc

        wait_timeout = transaction_timeout + self._submit_timeout + 1.0
        if not call.done.wait(wait_timeout):
            raise ModemOwnerError(
                f"timed out waiting for modem owner operation {operation!r}"
            )
        if call.error is not None:
            raise ModemOwnerError(f"modem owner operation {operation!r} failed") from call.error
        return call.result

    def _run(self) -> None:
        transport: ModemTransport | None = None
        try:
            self._owner_thread_id = threading.get_ident()
            transport = self._transport_factory()
            with self._lock:
                self._accepting = True
            self._ready.set()

            while True:
                item = self._queue.get()
                try:
                    if item is _STOP:
                        return
                    call = cast(_Call, item)
                    try:
                        call.result = self._dispatch(transport, call)
                    except BaseException as exc:
                        call.error = exc
                    finally:
                        call.done.set()
                finally:
                    self._queue.task_done()
        except BaseException as exc:
            self._start_error = exc
            self._ready.set()
        finally:
            with self._lock:
                self._accepting = False
            if transport is not None:
                try:
                    transport.close()
                finally:
                    pass
            self._stopped.set()

    def _dispatch(self, transport: ModemTransport, call: _Call) -> object | None:
        if threading.get_ident() != self._owner_thread_id:
            raise RuntimeError("modem transport dispatch escaped the owner thread")

        if call.operation == "get_version":
            response = self._transact(transport, protocol.get_version_request(), call.timeout)
            return protocol.parse_version_response(response)
        if call.operation == "set_rx_frequency":
            frequency_hz = cast(int, call.argument)
            response = self._transact(
                transport,
                rx_config.set_rx_frequency_request(frequency_hz),
                call.timeout,
            )
            protocol.parse_ack(response, expected_command=protocol.SET_FREQ)
            return None
        if call.operation == "arm_rx_modem_io":
            response = self._transact(
                transport,
                rx_config.arm_rx_modem_io_request(),
                call.timeout,
            )
            protocol.parse_ack(response, expected_command=protocol.SET_CONFIG)
            return None
        if call.operation == "rx_start":
            response = self._transact(transport, protocol.rx_start_request(), call.timeout)
            protocol.parse_ack(response, expected_command=protocol.YWD_RX)
            return None
        if call.operation == "rx_read":
            maximum = cast(int, call.argument)
            request = protocol.rx_read_request(maximum)
            response = self._transact(transport, request, call.timeout)
            return protocol.parse_rx_read(response)
        if call.operation == "rx_status":
            response = self._transact(transport, protocol.rx_status_request(), call.timeout)
            return protocol.parse_rx3_status(response)
        if call.operation == "rx_rssi":
            response = self._transact(transport, protocol.rx_rssi_request(), call.timeout)
            return protocol.parse_rx_rssi(response)
        if call.operation == "rx_stop":
            response = self._transact(transport, protocol.rx_stop_request(), call.timeout)
            protocol.parse_ack(response, expected_command=protocol.YWD_RX)
            return None
        if call.operation == "rf_status":
            response = self._transact(transport, protocol.rf_status_request(), call.timeout)
            return protocol.parse_rf_status(response)
        if call.operation == "rf_diag":
            response = self._transact(transport, protocol.rf_diag_request(), call.timeout)
            return protocol.parse_rf_diagnostics(response)
        raise RuntimeError(f"unsupported owner operation: {call.operation}")

    def _transact(self, transport: ModemTransport, request: bytes, timeout: float) -> bytes:
        response = transport.transact(request, timeout=timeout)
        with self._lock:
            self._transactions += 1
        return response
