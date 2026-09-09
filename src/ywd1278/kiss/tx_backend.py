"""KISS TCP backend for guarded 0C-P7 DATA admission.

This extends the host-qualified P6 control-aware backend.  Port-0 KISS DATA is
no longer blindly rejected: it captures the current immutable P6 parameter
generation and attempts admission into an injected ``KISSDataAdmissionQueue``.
The backend itself does not observe RSSI and cannot dispatch a queued frame;
there is still no modem/UART/RF dependency here.
"""

from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Callable

from .control import TNCControlBackend, TNCControlResult, TNCSessionState
from .framing import DATA, KISSMessage
from .server import PacketEvent
from .tx_path import (
    KISSDataAdmissionError,
    KISSDataAdmissionQueue,
    KISSDataFrameRejected,
    KISSDataQueueFull,
    KISSDataRequestReceipt,
    KISSDataTimeError,
)


MonotonicClock = Callable[[], float]


@dataclass(frozen=True)
class KISSDataIngressCounters:
    data_messages_received: int
    data_admitted: int
    data_invalid_rejections: int
    data_queue_full_drops: int
    data_time_rejections: int
    data_other_rejections: int


@dataclass(frozen=True)
class KISSDataIngressResult:
    admitted: bool
    receipt: KISSDataRequestReceipt | None
    reason: str


class TNCTransmitBackend(TNCControlBackend):
    """P6 controls plus bounded P7 KISS DATA admission, still host-only."""

    def __init__(
        self,
        admission: KISSDataAdmissionQueue,
        *,
        monotonic: MonotonicClock,
        events: tuple[PacketEvent, ...] | list[PacketEvent] = (),
        session: TNCSessionState | None = None,
        history_capacity: int = 256,
        subscriber_queue_capacity: int = 64,
    ) -> None:
        super().__init__(
            events,
            session=session,
            history_capacity=history_capacity,
            subscriber_queue_capacity=subscriber_queue_capacity,
        )
        self.admission = admission
        self._monotonic = monotonic
        self._ingress_lock = threading.Lock()
        self._data_messages_received = 0
        self._data_admitted = 0
        self._data_invalid_rejections = 0
        self._data_queue_full_drops = 0
        self._data_time_rejections = 0
        self._data_other_rejections = 0

    @property
    def ingress_counters(self) -> KISSDataIngressCounters:
        with self._ingress_lock:
            return KISSDataIngressCounters(
                data_messages_received=self._data_messages_received,
                data_admitted=self._data_admitted,
                data_invalid_rejections=self._data_invalid_rejections,
                data_queue_full_drops=self._data_queue_full_drops,
                data_time_rejections=self._data_time_rejections,
                data_other_rejections=self._data_other_rejections,
            )

    def reject_client_message(
        self,
        message: KISSMessage,
    ) -> KISSDataIngressResult | TNCControlResult:
        """Handle one decoded KISS message at the P7 admission boundary.

        The historical server method name is retained for compatibility.  P7
        overrides only port-0 DATA behavior; all controls and unsupported ports
        still flow through the exact P6 implementation.
        """

        if message.command != DATA or message.port != 0:
            return super().reject_client_message(message)

        with self._ingress_lock:
            self._data_messages_received += 1

        context = self.session.capture_tx_context(
            max_wait_seconds=self.admission.request_timeout_seconds,
        )
        try:
            receipt = self.admission.enqueue(
                message.frame,
                context,
                now=float(self._monotonic()),
            )
        except KISSDataFrameRejected as exc:
            with self._ingress_lock:
                self._data_invalid_rejections += 1
            return KISSDataIngressResult(False, None, str(exc))
        except KISSDataQueueFull as exc:
            with self._ingress_lock:
                self._data_queue_full_drops += 1
            return KISSDataIngressResult(False, None, str(exc))
        except KISSDataTimeError as exc:
            with self._ingress_lock:
                self._data_time_rejections += 1
            return KISSDataIngressResult(False, None, str(exc))
        except KISSDataAdmissionError as exc:
            with self._ingress_lock:
                self._data_other_rejections += 1
            return KISSDataIngressResult(False, None, str(exc))

        with self._ingress_lock:
            self._data_admitted += 1
        return KISSDataIngressResult(
            True,
            receipt,
            "port-0 KISS DATA admitted with immutable P6 parameter snapshot",
        )
