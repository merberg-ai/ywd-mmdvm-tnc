"""Deterministic 0C-P3 bridge from qualified RSSI busy state to P1 CSMA.

This module composes two already-qualified pure state machines without adding
I/O or transmit capability:

raw RSSI -> RSSIChannelBusyDetector -> channel_busy bool -> PersistentCSMA

Time and persistence randomness remain caller supplied.  No modem, UART,
thread, clock, sleep, RNG, KISS, broker, or RF dependency exists here.  The
adapter represents one CSMA access attempt and deliberately inherits P1's
single-use READY/TIMED_OUT terminal semantics.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .channel_busy import ChannelBusyDecision, RSSIChannelBusyDetector
from .csma import CSMADecision, CSMAParameters, CSMAState, PersistentCSMA


RandomByteSource = Callable[[], int]


class ChannelAccessRandomnessRequired(RuntimeError):
    """A clear persistence slot is due but no caller randomness was supplied."""


@dataclass(frozen=True)
class ChannelAccessObservation:
    """One combined RSSI-detector / P1-CSMA observation."""

    now: float
    detector: ChannelBusyDecision
    csma: CSMADecision
    random_byte: int | None


class ShadowChannelAccessAttempt:
    """Compose one detector instance with one unchanged P1 CSMA attempt.

    ``random_byte_source`` is consulted only when the detector reports CLEAR
    and the underlying P1 policy has a persistence slot due.  This preserves
    the P1 rule that randomness is supplied explicitly and never drawn while
    the channel is busy, during recent-RX hold, before a slot is due, after
    timeout, or after READY.

    The class is named ``Shadow`` because 0C-P3 does not authorize or invoke
    transmission.  READY means only that P1 *would* permit a caller to proceed
    at that instant; there is no broker or modem TX path in this module.
    """

    def __init__(
        self,
        *,
        started_at: float,
        parameters: CSMAParameters | None = None,
    ) -> None:
        started_at = float(started_at)
        self._detector = RSSIChannelBusyDetector(started_at=started_at)
        self._csma = PersistentCSMA(started_at=started_at, parameters=parameters)
        self._last_observation: ChannelAccessObservation | None = None

    @property
    def detector(self) -> RSSIChannelBusyDetector:
        return self._detector

    @property
    def csma(self) -> PersistentCSMA:
        return self._csma

    @property
    def observation(self) -> ChannelAccessObservation | None:
        return self._last_observation

    def observe_rssi(
        self,
        *,
        now: float,
        raw_magnitude: int,
        random_byte_source: RandomByteSource | None = None,
    ) -> ChannelAccessObservation:
        """Advance detector and P1 using one RSSI sample at explicit ``now``."""

        now = float(now)
        detector = self._detector.observe(now=now, raw_magnitude=raw_magnitude)

        random_byte: int | None = None
        prior = self._csma.decision
        persistence_due = (
            not detector.channel_busy
            and prior.state is CSMAState.WAIT_SLOT
            and prior.next_slot_at is not None
            and now >= prior.next_slot_at
            and now < prior.deadline_at
        )
        if persistence_due:
            if random_byte_source is None:
                raise ChannelAccessRandomnessRequired(
                    "clear persistence slot is due; caller must supply one random byte"
                )
            random_byte = int(random_byte_source())

        csma = self._csma.observe(
            now=now,
            channel_busy=detector.channel_busy,
            random_byte=random_byte,
        )
        observation = ChannelAccessObservation(
            now=now,
            detector=detector,
            csma=csma,
            random_byte=random_byte,
        )
        self._last_observation = observation
        return observation
