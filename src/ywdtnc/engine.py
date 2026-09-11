"""Modem-only composition over the qualified YWD-1278 packet core."""

from __future__ import annotations

from dataclasses import dataclass
import secrets
import threading
import time
from typing import Callable

from ywd1278.kiss.control import TNCControlBackend, TNCParameterSnapshot, TNCSessionState
from ywd1278.kiss.framing import DATA, KISSMessage
from ywd1278.kiss.server import PacketEvent, ThreadingKISSServer, start_server_thread, stop_server_thread
from ywd1278.kiss.sustained import SustainedTNCBackend, ThreadSafeKISSDataAdmissionQueue
from ywd1278.modem._serial import posix_serial_transport_factory
from ywd1278.modem.owner import TransportFactory
from ywd1278.service.tnc_runtime import SustainedTNCRuntime
from ywd1278.tx.contextual import ContextualHalfDuplexSubmitter, ContextualTXDelayRouter
from ywd1278.tx.half_duplex import HalfDuplexParameters

from . import QUALIFIED_FIRMWARE_IDENTITY
from .agw.server import ThreadingAGWServer, start_agw_server_thread, stop_agw_server_thread
from .config import TNCConfig, validate_config
from .monitor import (
    MonitorEventHub,
    ThreadingMonitorServer,
    frame_fields,
    start_monitor_server_thread,
    stop_monitor_server_thread,
)
from .monitor_tx import (
    MonitoredAdmissionQueue,
    MonitoredContextualLifecycle,
    MonitoredContextualRouter,
    TXRequestTracker,
)
from .rf_profiles import ProductTXModemOwner


MonotonicClock = Callable[[], float]
Sleeper = Callable[[float], None]
RandomByteSource = Callable[[], int]


class TNCEngineError(RuntimeError):
    pass


@dataclass(frozen=True)
class TNCEngineSnapshot:
    running: bool
    firmware_identity: str
    tx_enabled: bool
    decoded_rx_frames: int
    tx_dispatches: int
    tx_queue_depth: int
    kiss_listener: tuple[str, int] | None
    agw_listener: tuple[str, int] | None
    monitor_listener: tuple[str, int] | None
    failure: str


class ModemTNCBackend(SustainedTNCBackend):
    """Shared KISS/AGW backend with fail-closed TX and side-band monitoring."""

    def __init__(
        self,
        *args,
        transmit_enabled: bool,
        monitor_hub: MonitorEventHub | None = None,
        **kwargs,
    ) -> None:  # type: ignore[no-untyped-def]
        super().__init__(*args, **kwargs)
        self.transmit_enabled = bool(transmit_enabled)
        self.monitor_hub = monitor_hub if monitor_hub is not None else MonitorEventHub()

    def publish(self, event: PacketEvent) -> None:
        super().publish(event)
        self.monitor_hub.publish("rx.frame", **frame_fields(event.frame_no_fcs))

    def reject_client_message(self, message: KISSMessage):  # type: ignore[no-untyped-def]
        if message.port == 0 and message.command == DATA:
            self.monitor_hub.publish("tx.submitted", **frame_fields(message.frame))
            if not self.transmit_enabled:
                result = TNCControlBackend.reject_client_message(self, message)
                self.monitor_hub.publish(
                    "tx.rejected",
                    **frame_fields(message.frame),
                    reason="product TX is disabled",
                )
                return result
            result = super().reject_client_message(message)
            if getattr(result, "admitted", True) is False:
                self.monitor_hub.publish(
                    "tx.rejected",
                    **frame_fields(message.frame),
                    reason=str(getattr(result, "reason", "TX admission rejected")),
                )
            return result
        return super().reject_client_message(message)


class TNCEngine:
    """Own one modem UART and expose KISS, AGW raw, and passive monitor planes."""

    def __init__(
        self,
        config: TNCConfig,
        *,
        transport_factory: TransportFactory | None = None,
        monotonic: MonotonicClock = time.monotonic,
        sleep: Sleeper = time.sleep,
        random_byte_source: RandomByteSource | None = None,
    ) -> None:
        validate_config(config)
        self.config = config
        self._transport_factory = transport_factory or posix_serial_transport_factory(config.device)
        self._monotonic = monotonic
        self._sleep = sleep
        self._random_byte_source = random_byte_source or (lambda: secrets.randbelow(256))

        self.monitor_hub = MonitorEventHub(queue_capacity=256)
        self.tx_tracker = TXRequestTracker()
        self.owner: ProductTXModemOwner | None = None
        self.router: ContextualTXDelayRouter | None = None
        self.lifecycle: ContextualHalfDuplexSubmitter | None = None
        self.admission = None
        self.session: TNCSessionState | None = None
        self.backend: ModemTNCBackend | None = None
        self.runtime: SustainedTNCRuntime | None = None
        self.kiss_server: ThreadingKISSServer | None = None
        self.kiss_thread: threading.Thread | None = None
        self.agw_server: ThreadingAGWServer | None = None
        self.agw_thread: threading.Thread | None = None
        self.monitor_server: ThreadingMonitorServer | None = None
        self.monitor_thread: threading.Thread | None = None

        self._started = False
        self._stopped = False
        self._rx_started = False
        self._identity = ""

    @property
    def snapshot(self) -> TNCEngineSnapshot:
        runtime = self.runtime.runtime_counters if self.runtime is not None else None
        queue_depth = self.admission.snapshot.queue_depth if self.admission is not None else 0
        kiss_listener = None
        agw_listener = None
        monitor_listener = None
        if self.kiss_server is not None:
            host, port = self.kiss_server.server_address[:2]
            kiss_listener = (str(host), int(port))
        if self.agw_server is not None:
            host, port = self.agw_server.server_address[:2]
            agw_listener = (str(host), int(port))
        if self.monitor_server is not None:
            host, port = self.monitor_server.server_address[:2]
            monitor_listener = (str(host), int(port))
        running = bool(
            self.owner
            and self.owner.snapshot.running
            and runtime
            and runtime.running
            and (not self.config.kiss.enabled or (self.kiss_thread and self.kiss_thread.is_alive()))
            and (not self.config.agw.enabled or (self.agw_thread and self.agw_thread.is_alive()))
            and (not self.config.monitor.enabled or (self.monitor_thread and self.monitor_thread.is_alive()))
        )
        return TNCEngineSnapshot(
            running=running,
            firmware_identity=self._identity,
            tx_enabled=self.config.tx_enabled,
            decoded_rx_frames=0 if runtime is None else runtime.decoded_rx_frames,
            tx_dispatches=0 if runtime is None else runtime.tx_dispatches,
            tx_queue_depth=queue_depth,
            kiss_listener=kiss_listener,
            agw_listener=agw_listener,
            monitor_listener=monitor_listener,
            failure="" if runtime is None else runtime.failure,
        )

    def start(self) -> None:
        if self._started:
            raise TNCEngineError("TNC engine cannot be restarted")
        self._started = True

        owner = ProductTXModemOwner(
            self._transport_factory,
            queue_capacity=16,
            submit_timeout=0.20,
            default_transaction_timeout=1.50,
        )
        self.owner = owner
        try:
            owner.start(timeout=2.0)
            version = owner.get_version(timeout=1.5)
            if version.identity != self.config.required_identity:
                raise TNCEngineError(
                    f"packet firmware identity mismatch: expected={self.config.required_identity!r} "
                    f"actual={version.identity!r}"
                )
            if version.identity != QUALIFIED_FIRMWARE_IDENTITY:
                raise TNCEngineError("runtime identity is outside the qualified AX25R4 lineage")
            self._identity = version.identity

            rf_status = owner.rf_status(timeout=1.5)
            rf_diag = owner.rf_diagnostics(timeout=1.5)
            if rf_status.remaining_selectors != 0 or rf_diag.tx_active != 0:
                raise TNCEngineError("modem RF path is not idle before startup")

            if self.config.tx_enabled:
                owner.apply_tx_profile(
                    self.config.frequency_hz,
                    self.config.tx_power,
                    timeout=1.5,
                )
            else:
                owner.set_rx_frequency(self.config.frequency_hz, timeout=1.5)
            owner.arm_rx_modem_io(timeout=1.5)
            owner.rx_start(timeout=1.5)
            self._rx_started = True

            router = ContextualTXDelayRouter(
                owner,
                transmit_enabled=self.config.tx_enabled,
                broker_queue_capacity=1,
                broker_submit_timeout=0.05,
                default_transaction_timeout=1.5,
            )
            self.router = router
            monitored_router = MonitoredContextualRouter(router, self.monitor_hub, self.tx_tracker)
            lifecycle = ContextualHalfDuplexSubmitter(
                owner,
                monitored_router,  # type: ignore[arg-type]
                monotonic=self._monotonic,
                sleep=self._sleep,
                parameters=HalfDuplexParameters(
                    transaction_timeout_seconds=1.5,
                    tx_idle_poll_seconds=0.05,
                    tx_idle_timeout_seconds=5.0,
                ),
            )
            self.lifecycle = lifecycle
            monitored_lifecycle = MonitoredContextualLifecycle(
                lifecycle, self.monitor_hub, self.tx_tracker
            )
            base_admission = ThreadSafeKISSDataAdmissionQueue(
                monitored_lifecycle,  # type: ignore[arg-type]
                monotonic=self._monotonic,
                queue_capacity=4,
                request_timeout_seconds=30.0,
                downstream_timeout_seconds=1.5,
            )
            admission = MonitoredAdmissionQueue(base_admission, self.monitor_hub, self.tx_tracker)
            self.admission = admission
            session = TNCSessionState(
                TNCParameterSnapshot(
                    txdelay=self.config.txdelay,
                    persist=self.config.persist,
                    slottime=self.config.slottime,
                )
            )
            self.session = session
            backend = ModemTNCBackend(
                admission,  # type: ignore[arg-type]
                monotonic=self._monotonic,
                session=session,
                history_capacity=0,
                subscriber_queue_capacity=64,
                transmit_enabled=self.config.tx_enabled,
                monitor_hub=self.monitor_hub,
            )
            self.backend = backend
            runtime = SustainedTNCRuntime(
                owner,
                backend,
                admission,  # type: ignore[arg-type]
                expected_identity=self.config.required_identity,
                monotonic=self._monotonic,
                random_byte_source=self._random_byte_source,
                read_maximum=200,
                idle_sleep_seconds=0.005,
                status_interval_seconds=0.25,
                thread_name="ywd-tncd-modem",
            )
            self.runtime = runtime
            runtime.start(timeout=1.5)

            if self.config.kiss.enabled:
                self.kiss_server, self.kiss_thread = start_server_thread(
                    backend,
                    host=self.config.kiss.listen,
                    port=self.config.kiss.port,
                )
            if self.config.agw.enabled:
                self.agw_server, self.agw_thread = start_agw_server_thread(
                    backend,
                    host=self.config.agw.listen,
                    port=self.config.agw.port,
                )
            if self.config.monitor.enabled:
                self.monitor_server, self.monitor_thread = start_monitor_server_thread(
                    self.monitor_hub,
                    host=self.config.monitor.listen,
                    port=self.config.monitor.port,
                )
            self.check_health()
        except BaseException:
            self._cleanup(suppress_errors=True)
            raise

    def check_health(self) -> None:
        if self.owner is None or self.runtime is None:
            raise TNCEngineError("TNC engine is not started")
        if not self.owner.snapshot.running:
            raise TNCEngineError("modem owner is not running")
        self.runtime.check_health()
        if self.config.kiss.enabled and not (self.kiss_thread and self.kiss_thread.is_alive()):
            raise TNCEngineError("KISS listener is not running")
        if self.config.agw.enabled and not (self.agw_thread and self.agw_thread.is_alive()):
            raise TNCEngineError("AGW listener is not running")
        if self.config.monitor.enabled and not (
            self.monitor_thread and self.monitor_thread.is_alive()
        ):
            raise TNCEngineError("monitor listener is not running")

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        errors = self._cleanup(suppress_errors=False)
        if errors:
            raise TNCEngineError("one or more TNC shutdown operations failed") from errors[0]

    def _cleanup(self, *, suppress_errors: bool) -> list[BaseException]:
        errors: list[BaseException] = []
        if self.monitor_server is not None and self.monitor_thread is not None:
            try:
                stop_monitor_server_thread(self.monitor_server, self.monitor_thread)
            except BaseException as exc:
                errors.append(exc)
            finally:
                self.monitor_server = None
                self.monitor_thread = None
        if self.agw_server is not None and self.agw_thread is not None:
            try:
                stop_agw_server_thread(self.agw_server, self.agw_thread)
            except BaseException as exc:
                errors.append(exc)
            finally:
                self.agw_server = None
                self.agw_thread = None
        if self.kiss_server is not None and self.kiss_thread is not None:
            try:
                stop_server_thread(self.kiss_server, self.kiss_thread)
            except BaseException as exc:
                errors.append(exc)
            finally:
                self.kiss_server = None
                self.kiss_thread = None
        if self.runtime is not None:
            try:
                self.runtime.stop(timeout=3.0)
            except BaseException as exc:
                errors.append(exc)
        if self.router is not None:
            try:
                self.router.close()
            except BaseException as exc:
                errors.append(exc)
        if self.owner is not None:
            if self._rx_started:
                try:
                    self.owner.rx_stop(timeout=1.5)
                except BaseException as exc:
                    errors.append(exc)
                finally:
                    self._rx_started = False
            try:
                self.owner.stop(timeout=2.0)
            except BaseException as exc:
                errors.append(exc)
        return [] if suppress_errors else errors
