"""Fail-closed persistent half-duplex RX/TX lifecycle coordinator.

0C-P4e turns the one-shot P4d RX_STOP -> TX -> idle handoff into a reusable
host-side boundary suitable for a persistent packet service.  It deliberately
remains below channel access and above the already-qualified TX broker:

    qualified READY -> RX_STOP -> downstream TX -> wait RF idle -> RX_START

The coordinator never decides when a frame may transmit, never retries a frame,
and never owns a modem transport.  It receives only typed modem operations and
an injected frame submitter.  Time and sleeping are injected so deterministic
CI can exercise every handoff without a UART or wall clock.

A failure after the downstream submitter has accepted a frame is especially
important: the frame may already have gone over RF, so the coordinator latches
failed and must never resubmit it.  A caller must reconstruct the lifecycle
boundary after repairing/reinitializing RX state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from ywd1278.modem import protocol


ACTIVE_RX_REQUIRED_MASK = 0x0D  # active + RF-ready + STATE_AX25
RX_ACTIVE_FLAG = 0x01
TX_ACTIVE_FLAG = 0x02


class HalfDuplexModemPort(Protocol):
    """Typed modem operations needed by the half-duplex lifecycle."""

    def rx_status(self, *, timeout: float | None = None) -> protocol.RX3Status: ...

    def rx_stop(self, *, timeout: float | None = None) -> None: ...

    def rx_start(self, *, timeout: float | None = None) -> None: ...

    def rf_status(self, *, timeout: float | None = None) -> protocol.RFStatus: ...

    def rf_diagnostics(self, *, timeout: float | None = None) -> protocol.RFDiagnostics: ...


class FrameSubmitter(Protocol):
    """Existing downstream frame boundary, satisfied directly by TXBroker."""

    def submit_frame(self, frame_with_fcs: bytes, *, timeout: float | None = None) -> object: ...


MonotonicClock = Callable[[], float]
Sleeper = Callable[[float], None]


class HalfDuplexError(RuntimeError):
    pass


class HalfDuplexLatched(HalfDuplexError):
    pass


class HalfDuplexPreTransmitError(HalfDuplexError):
    """RX could not be safely handed off before downstream TX was called."""


class HalfDuplexDownstreamError(HalfDuplexError):
    """Downstream TX failed; the frame is terminal and is never retried here."""


class HalfDuplexPostTransmitError(HalfDuplexError):
    """TX was accepted but idle/RX restoration failed afterward."""

    transmission_accepted = True


@dataclass(frozen=True)
class HalfDuplexParameters:
    transaction_timeout_seconds: float = 1.5
    tx_idle_poll_seconds: float = 0.05
    tx_idle_timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        if self.transaction_timeout_seconds <= 0.0:
            raise ValueError("transaction_timeout_seconds must be positive")
        if self.tx_idle_poll_seconds <= 0.0:
            raise ValueError("tx_idle_poll_seconds must be positive")
        if self.tx_idle_timeout_seconds <= 0.0:
            raise ValueError("tx_idle_timeout_seconds must be positive")


@dataclass(frozen=True)
class HalfDuplexSnapshot:
    in_cycle: bool
    failed_latched: bool
    cycles_started: int
    cycles_completed: int
    downstream_accepted: int
    pre_transmit_failures: int
    downstream_failures: int
    post_transmit_failures: int
    recovered_downstream_failures: int
    rx_stop_operations: int
    rx_restart_operations: int
    tx_idle_polls: int


class PersistentHalfDuplexSubmitter:
    """Reusable READY-consumer that restores passive RX after every safe TX.

    The object is intentionally synchronous.  The outer P4a access queue calls
    ``submit_frame`` only after P1 reaches READY.  This method then performs one
    complete half-duplex cycle and returns the downstream receipt only after RF
    is idle and passive RX is active again.

    There is no frame retry.  Downstream failure is terminal for that request.
    If RF can be proven idle and RX can be restarted after such a failure, the
    coordinator remains usable for later *different* requests.  Any uncertain
    handoff or any failure after a successful downstream acceptance latches the
    coordinator failed and blocks future submissions.
    """

    def __init__(
        self,
        modem: HalfDuplexModemPort,
        submitter: FrameSubmitter,
        *,
        monotonic: MonotonicClock,
        sleep: Sleeper,
        parameters: HalfDuplexParameters | None = None,
    ) -> None:
        self._modem = modem
        self._submitter = submitter
        self._monotonic = monotonic
        self._sleep = sleep
        self._parameters = parameters or HalfDuplexParameters()

        self._in_cycle = False
        self._failed_latched = False
        self._cycles_started = 0
        self._cycles_completed = 0
        self._downstream_accepted = 0
        self._pre_transmit_failures = 0
        self._downstream_failures = 0
        self._post_transmit_failures = 0
        self._recovered_downstream_failures = 0
        self._rx_stop_operations = 0
        self._rx_restart_operations = 0
        self._tx_idle_polls = 0

    @property
    def snapshot(self) -> HalfDuplexSnapshot:
        return HalfDuplexSnapshot(
            in_cycle=self._in_cycle,
            failed_latched=self._failed_latched,
            cycles_started=self._cycles_started,
            cycles_completed=self._cycles_completed,
            downstream_accepted=self._downstream_accepted,
            pre_transmit_failures=self._pre_transmit_failures,
            downstream_failures=self._downstream_failures,
            post_transmit_failures=self._post_transmit_failures,
            recovered_downstream_failures=self._recovered_downstream_failures,
            rx_stop_operations=self._rx_stop_operations,
            rx_restart_operations=self._rx_restart_operations,
            tx_idle_polls=self._tx_idle_polls,
        )

    def submit_frame(self, frame_with_fcs: bytes, *, timeout: float | None = None) -> object:
        if self._failed_latched:
            raise HalfDuplexLatched("half-duplex lifecycle is latched failed")
        if self._in_cycle:
            raise HalfDuplexLatched("half-duplex lifecycle already has an in-flight cycle")

        transaction_timeout = (
            self._parameters.transaction_timeout_seconds if timeout is None else float(timeout)
        )
        if transaction_timeout <= 0.0:
            raise ValueError("transaction timeout must be positive")

        self._in_cycle = True
        self._cycles_started += 1
        downstream_accepted = False
        try:
            try:
                self._require_active_rx(
                    self._modem.rx_status(timeout=transaction_timeout),
                    context="before RX_STOP",
                )
                self._modem.rx_stop(timeout=transaction_timeout)
                self._rx_stop_operations += 1
                self._require_inactive_rx(
                    self._modem.rx_status(timeout=transaction_timeout),
                    context="after RX_STOP",
                )
            except BaseException as exc:
                self._pre_transmit_failures += 1
                self._failed_latched = True
                raise HalfDuplexPreTransmitError(
                    "could not prove a safe RX_STOP handoff; downstream TX was not called"
                ) from exc

            try:
                result = self._submitter.submit_frame(
                    bytes(frame_with_fcs),
                    timeout=transaction_timeout,
                )
                downstream_accepted = True
                self._downstream_accepted += 1
            except BaseException as exc:
                self._downstream_failures += 1
                # Never retry this frame.  Recover RX only after proving RF idle.
                try:
                    self._wait_for_tx_idle(transaction_timeout=transaction_timeout)
                    self._restart_and_verify_rx(transaction_timeout=transaction_timeout)
                    self._recovered_downstream_failures += 1
                except BaseException as recovery_exc:
                    self._failed_latched = True
                    raise HalfDuplexDownstreamError(
                        "downstream TX failed and safe RX recovery also failed; frame is terminal"
                    ) from recovery_exc
                raise HalfDuplexDownstreamError(
                    "downstream TX failed; RX recovered and frame is terminal without retry"
                ) from exc

            try:
                self._wait_for_tx_idle(transaction_timeout=transaction_timeout)
                self._restart_and_verify_rx(transaction_timeout=transaction_timeout)
            except BaseException as exc:
                # At this point TX was accepted.  Never permit a caller to infer
                # that retrying the same frame is safe.
                self._post_transmit_failures += 1
                self._failed_latched = True
                raise HalfDuplexPostTransmitError(
                    "TX was accepted but RF-idle/RX restoration failed; do not retry the frame"
                ) from exc

            self._cycles_completed += 1
            return result
        finally:
            # ``downstream_accepted`` exists to make the post-TX boundary
            # explicit during review; counters above are authoritative.
            _ = downstream_accepted
            self._in_cycle = False

    def _wait_for_tx_idle(self, *, transaction_timeout: float) -> None:
        started = float(self._monotonic())
        deadline = started + self._parameters.tx_idle_timeout_seconds
        while True:
            status = self._modem.rf_status(timeout=transaction_timeout)
            diag = self._modem.rf_diagnostics(timeout=transaction_timeout)
            self._tx_idle_polls += 1
            if status.remaining_selectors == 0 and diag.tx_active == 0:
                return
            now = float(self._monotonic())
            if now >= deadline:
                raise HalfDuplexPostTransmitError("timed out waiting for RF TX to become idle")
            delay = min(self._parameters.tx_idle_poll_seconds, max(0.0, deadline - now))
            if delay <= 0.0:
                raise HalfDuplexPostTransmitError("timed out waiting for RF TX to become idle")
            self._sleep(delay)

    def _restart_and_verify_rx(self, *, transaction_timeout: float) -> None:
        self._modem.rx_start(timeout=transaction_timeout)
        self._rx_restart_operations += 1
        self._require_active_rx(
            self._modem.rx_status(timeout=transaction_timeout),
            context="after RX_START",
        )

    @staticmethod
    def _require_active_rx(status: protocol.RX3Status, *, context: str) -> None:
        if status.dropped_bytes != 0:
            raise HalfDuplexError(
                f"RX FIFO has dropped bytes {context}: {status.dropped_bytes}"
            )
        if status.flags & TX_ACTIVE_FLAG:
            raise HalfDuplexError(
                f"RX status reports TX active {context}: flags=0x{status.flags:02X}"
            )
        if status.flags & ACTIVE_RX_REQUIRED_MASK != ACTIVE_RX_REQUIRED_MASK:
            raise HalfDuplexError(
                f"passive AX.25 RX is not fully active {context}: "
                f"flags=0x{status.flags:02X} required=0x{ACTIVE_RX_REQUIRED_MASK:02X}"
            )

    @staticmethod
    def _require_inactive_rx(status: protocol.RX3Status, *, context: str) -> None:
        if status.dropped_bytes != 0:
            raise HalfDuplexError(
                f"RX FIFO has dropped bytes {context}: {status.dropped_bytes}"
            )
        if status.flags & RX_ACTIVE_FLAG:
            raise HalfDuplexError(
                f"RX capture remained active {context}: flags=0x{status.flags:02X}"
            )
        if status.flags & TX_ACTIVE_FLAG:
            raise HalfDuplexError(
                f"RX status reports TX active {context}: flags=0x{status.flags:02X}"
            )
