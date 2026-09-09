"""Read-only live RSSI -> detector -> P1 CSMA sidecar for 0C-P3.

This service layer attaches to an already-running RXOnlyPacketRuntime and shares
its existing base ModemOwner.  It does not start, stop, configure, or otherwise
own the RX lifecycle.  One call to :meth:`sample` performs exactly one typed
``owner.rx_rssi()`` transaction and feeds that raw magnitude into the pure
ShadowChannelAccessAttempt.

There is deliberately no thread, sleep, hidden clock, RNG, TX owner, broker,
KISS connection, or RF transmit operation here.  A caller controls sampling
time and supplies persistence randomness only when the unchanged P1 policy
needs it.
"""

from __future__ import annotations

from dataclasses import dataclass

from ywd1278.modem.owner import ModemOwner
from ywd1278.tx.channel_access import (
    ChannelAccessObservation,
    RandomByteSource,
    ShadowChannelAccessAttempt,
)
from ywd1278.tx.csma import CSMAParameters

from .rx_runtime import ACTIVE_RX_FLAGS


class LiveChannelAccessError(RuntimeError):
    """The shadow channel-access sidecar cannot safely observe live RX."""


@dataclass(frozen=True)
class LiveChannelAccessSnapshot:
    samples: int
    last_raw_magnitude: int | None
    last_observation: ChannelAccessObservation | None


class LiveChannelAccessSampler:
    """Synchronous read-only sidecar sharing one running base ModemOwner."""

    def __init__(
        self,
        owner: ModemOwner,
        *,
        started_at: float,
        parameters: CSMAParameters | None = None,
    ) -> None:
        self._owner = owner
        self._attempt = ShadowChannelAccessAttempt(
            started_at=started_at,
            parameters=parameters,
        )
        self._samples = 0
        self._last_raw: int | None = None

    @property
    def attempt(self) -> ShadowChannelAccessAttempt:
        return self._attempt

    @property
    def snapshot(self) -> LiveChannelAccessSnapshot:
        return LiveChannelAccessSnapshot(
            samples=self._samples,
            last_raw_magnitude=self._last_raw,
            last_observation=self._attempt.observation,
        )

    def preflight(self, *, timeout: float = 1.0) -> None:
        """Require an already-running, active, loss-free RX runtime."""

        owner = self._owner.snapshot
        if not owner.running or owner.owner_thread_id is None:
            raise LiveChannelAccessError(
                "live channel-access sampler requires an already-running ModemOwner"
            )
        status = self._owner.rx_status(timeout=timeout)
        if status.flags != ACTIVE_RX_FLAGS:
            raise LiveChannelAccessError(
                f"packet RX is not active: expected=0x{ACTIVE_RX_FLAGS:02x} "
                f"actual=0x{status.flags:02x}"
            )
        if status.dropped_bytes != 0:
            raise LiveChannelAccessError(
                f"packet RX FIFO already reports {status.dropped_bytes} dropped bytes"
            )

    def sample(
        self,
        *,
        now: float,
        random_byte_source: RandomByteSource | None = None,
        timeout: float | None = None,
    ) -> ChannelAccessObservation:
        """Read one RSSI sample and advance the shadow access attempt."""

        owner = self._owner.snapshot
        if not owner.running or owner.owner_thread_id is None:
            raise LiveChannelAccessError("ModemOwner stopped during channel-access observation")

        raw = self._owner.rx_rssi(timeout=timeout).raw_magnitude
        observation = self._attempt.observe_rssi(
            now=now,
            raw_magnitude=raw,
            random_byte_source=random_byte_source,
        )
        self._samples += 1
        self._last_raw = raw
        return observation
