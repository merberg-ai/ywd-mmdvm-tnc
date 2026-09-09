"""Context-aware TX adapters for YWD-1278 0C-P7.

P7 must preserve one KISS parameter generation from DATA admission through RF
serialization without modifying the frozen P4e lifecycle or P5 TXDELAY policy.
This module provides two narrow adapters:

* ``ContextualTXDelayRouter`` lazily owns one qualified ``TXDelayBroker`` per
  TXDELAY byte actually used and synchronously routes a request to the broker
  selected by its immutable context.
* ``ContextualHalfDuplexSubmitter`` arms exactly one immutable context around
  one call to the already-qualified ``PersistentHalfDuplexSubmitter``.  The P4e
  coordinator therefore remains responsible for RX_STOP -> TX -> RF-idle ->
  RX_START and retains its persistent fail-latched state across requests.

All TX authority remains construction-time and fail-closed.  The router
defaults ``transmit_enabled=False``.  P7 host qualification enables it only
against fake modem ports; physical authorization belongs to a later guarded
qualification harness.
"""

from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Protocol

from ywd1278.tx.broker import TXModemPort, TXReceipt
from ywd1278.tx.half_duplex import (
    HalfDuplexModemPort,
    HalfDuplexParameters,
    HalfDuplexSnapshot,
    MonotonicClock,
    PersistentHalfDuplexSubmitter,
    Sleeper,
)
from ywd1278.tx.txdelay import TXDelayBroker, TXDelayProfile


class TXDelayContext(Protocol):
    """Minimum immutable context required by the TXDELAY router."""

    @property
    def txdelay_profile(self) -> TXDelayProfile: ...


@dataclass(frozen=True)
class ContextualRouterSnapshot:
    transmit_enabled: bool
    broker_profiles: tuple[int, ...]
    contextual_submissions: int
    contextual_failures: int


class ContextualTXDelayRouter:
    """Route one request through the qualified broker for its captured TXDELAY."""

    def __init__(
        self,
        owner: TXModemPort,
        *,
        transmit_enabled: bool = False,
        broker_queue_capacity: int = 1,
        broker_submit_timeout: float = 0.05,
        default_transaction_timeout: float = 1.5,
    ) -> None:
        if broker_queue_capacity < 1:
            raise ValueError("broker_queue_capacity must be at least 1")
        self._owner = owner
        self._transmit_enabled = bool(transmit_enabled)
        self._broker_queue_capacity = int(broker_queue_capacity)
        self._broker_submit_timeout = float(broker_submit_timeout)
        self._default_transaction_timeout = float(default_transaction_timeout)
        self._lock = threading.RLock()
        self._brokers: dict[int, TXDelayBroker] = {}
        self._closed = False
        self._submissions = 0
        self._failures = 0

    @property
    def snapshot(self) -> ContextualRouterSnapshot:
        with self._lock:
            return ContextualRouterSnapshot(
                transmit_enabled=self._transmit_enabled,
                broker_profiles=tuple(sorted(self._brokers)),
                contextual_submissions=self._submissions,
                contextual_failures=self._failures,
            )

    def submit_frame(
        self,
        frame_with_fcs: bytes,
        context: TXDelayContext,
        *,
        timeout: float | None = None,
    ) -> TXReceipt:
        """Synchronously submit through the broker matching captured TXDELAY."""

        with self._lock:
            if self._closed:
                raise RuntimeError("contextual TXDELAY router is closed")
            units = context.txdelay_profile.units
            broker = self._brokers.get(units)
            if broker is None:
                broker = TXDelayBroker(
                    self._owner,
                    txdelay_units=units,
                    transmit_enabled=self._transmit_enabled,
                    queue_capacity=self._broker_queue_capacity,
                    submit_timeout=self._broker_submit_timeout,
                    default_transaction_timeout=self._default_transaction_timeout,
                    thread_name=f"ywd1278-p7-txdelay-{units}",
                )
                broker.start()
                self._brokers[units] = broker

            try:
                receipt = broker.submit_frame(bytes(frame_with_fcs), timeout=timeout)
            except BaseException:
                self._failures += 1
                raise
            self._submissions += 1
            return receipt

    def close(self) -> None:
        """Stop every lazily-created qualified broker; idempotent."""

        with self._lock:
            if self._closed:
                return
            self._closed = True
            brokers = tuple(self._brokers.values())
        errors: list[BaseException] = []
        for broker in brokers:
            try:
                broker.stop()
            except BaseException as exc:
                errors.append(exc)
        if errors:
            raise RuntimeError("one or more contextual TXDELAY brokers failed to stop") from errors[0]

    def __enter__(self) -> "ContextualTXDelayRouter":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        self.close()


class _ArmedContextAdapter:
    """P4e-compatible submitter that can be armed for exactly one outer call."""

    def __init__(self, router: ContextualTXDelayRouter) -> None:
        self._router = router
        self._context: TXDelayContext | None = None

    def arm(self, context: TXDelayContext) -> None:
        if self._context is not None:
            raise RuntimeError("context adapter is already armed")
        self._context = context

    def disarm(self) -> None:
        self._context = None

    def submit_frame(self, frame_with_fcs: bytes, *, timeout: float | None = None) -> object:
        context = self._context
        if context is None:
            raise RuntimeError("P4e downstream called without an armed P7 context")
        return self._router.submit_frame(frame_with_fcs, context, timeout=timeout)


class ContextualHalfDuplexSubmitter:
    """Preserve captured request context through the exact P4e lifecycle."""

    def __init__(
        self,
        modem: HalfDuplexModemPort,
        router: ContextualTXDelayRouter,
        *,
        monotonic: MonotonicClock,
        sleep: Sleeper,
        parameters: HalfDuplexParameters | None = None,
    ) -> None:
        self._router = router
        self._adapter = _ArmedContextAdapter(router)
        self._lifecycle = PersistentHalfDuplexSubmitter(
            modem,
            self._adapter,
            monotonic=monotonic,
            sleep=sleep,
            parameters=parameters,
        )
        self._submit_lock = threading.Lock()

    @property
    def half_duplex_snapshot(self) -> HalfDuplexSnapshot:
        return self._lifecycle.snapshot

    @property
    def router_snapshot(self) -> ContextualRouterSnapshot:
        return self._router.snapshot

    def submit_frame(
        self,
        frame_with_fcs: bytes,
        context: TXDelayContext,
        *,
        timeout: float | None = None,
    ) -> object:
        # P7's access queue is synchronous and single-head, but the explicit
        # lock also prevents accidental concurrent direct callers from swapping
        # the armed immutable context under the P4e coordinator.
        with self._submit_lock:
            self._adapter.arm(context)
            try:
                return self._lifecycle.submit_frame(
                    bytes(frame_with_fcs),
                    timeout=timeout,
                )
            finally:
                self._adapter.disarm()
