"""Deterministic RSSI channel-busy/recent-RX detector for YWD-1278.

0C-P2 is deliberately host-only. The constants below are grounded in the
physically qualified AX25R4 packet/RSSI correlation on the first supported HAT:
real decoded packet/transition samples occupied raw values through 70, while
the independent upper population began at 97. The physical descriptive
midpoint was 83.

This module does not read the modem, poll RSSI, own a UART, sleep, draw random
numbers, drive CSMA, submit TX, serve KISS, touch GPIO, or write firmware. A
caller supplies explicit monotonic time and one raw RSSI magnitude per
observation.

Polarity on this target is physically qualified as lower raw magnitude =
stronger RF. The detector therefore uses two thresholds entirely inside the
observed 70..97 empty guard region:

* raw <= 83 asserts BUSY immediately;
* raw >= 90 is eligible to release BUSY, but only after 250 ms continuously on
  the release side;
* raw 84..89 is the hysteresis band and retains the safe side of the current
  state. It never turns an UNKNOWN/BUSY/RECENT detector clear.

Startup is fail-closed. The detector is UNKNOWN/busy-for-access until the clear
release threshold has been continuously satisfied for the full recent-RX hold.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


BUSY_ASSERT_RAW_MAX = 83
CLEAR_RELEASE_RAW_MIN = 90
RECENT_RX_HOLD_SECONDS = 0.250

# Frozen physical anchors from the packet-correlated 0C-P2 evidence. These are
# kept separate from the operational thresholds so tests can prove the selected
# policy remains inside the observed guard region.
PHYSICAL_BUSY_SIDE_MAX = 70
PHYSICAL_UPPER_SIDE_MIN = 97
PHYSICAL_DESCRIPTIVE_MIDPOINT = 83
PHYSICAL_RSSI_POLL_SECONDS = 0.050


class ChannelBusyState(str, Enum):
    UNKNOWN = "unknown"
    BUSY = "busy"
    RECENT_RX = "recent-rx"
    CLEAR = "clear"


@dataclass(frozen=True)
class ChannelBusyDecision:
    state: ChannelBusyState
    raw_magnitude: int
    channel_busy: bool
    recent_rx: bool
    clear_candidate_since: float | None
    last_busy_at: float | None
    reason: str


class RSSIChannelBusyDetector:
    """Pure deterministic hysteretic channel-busy detector.

    ``observe`` is the only state transition operation. Time and raw RSSI are
    supplied by the caller so this safety boundary contains no hidden timing or
    I/O. Only CLEAR maps to ``channel_busy=False``; UNKNOWN and RECENT_RX fail
    closed just like BUSY.
    """

    def __init__(self, *, started_at: float = 0.0) -> None:
        started_at = float(started_at)
        if started_at < 0.0:
            raise ValueError("started_at must be >= 0")
        self._state = ChannelBusyState.UNKNOWN
        self._last_now = started_at
        self._clear_candidate_since: float | None = None
        self._last_busy_at: float | None = None
        self._last_decision: ChannelBusyDecision | None = None

    @property
    def state(self) -> ChannelBusyState:
        return self._state

    @property
    def decision(self) -> ChannelBusyDecision | None:
        return self._last_decision

    def observe(self, *, now: float, raw_magnitude: int) -> ChannelBusyDecision:
        now = float(now)
        raw = int(raw_magnitude)
        if now < self._last_now:
            raise ValueError("now must be monotonic for one detector instance")
        if not 0 <= raw <= 255:
            raise ValueError("raw_magnitude must be 0..255")
        self._last_now = now

        if raw <= BUSY_ASSERT_RAW_MAX:
            self._state = ChannelBusyState.BUSY
            self._last_busy_at = now
            self._clear_candidate_since = None
            return self._record(raw, "RSSI at/below busy-assert threshold")

        if raw < CLEAR_RELEASE_RAW_MIN:
            # True hysteresis band. Once CLEAR, stay CLEAR until the assert
            # threshold is crossed. From every non-clear state this band is not
            # sufficient evidence to start or continue release qualification.
            if self._state is not ChannelBusyState.CLEAR:
                self._clear_candidate_since = None
            return self._record(raw, "RSSI inside hysteresis band; retained prior safe state")

        # raw >= CLEAR_RELEASE_RAW_MIN
        if self._state is ChannelBusyState.CLEAR:
            return self._record(raw, "RSSI remains on clear-release side")

        if self._clear_candidate_since is None:
            self._clear_candidate_since = now
            self._state = ChannelBusyState.RECENT_RX
            return self._record(raw, "clear-release side observed; recent-RX hold started")

        if now - self._clear_candidate_since < RECENT_RX_HOLD_SECONDS:
            self._state = ChannelBusyState.RECENT_RX
            return self._record(raw, "clear-release side sustained; recent-RX hold not complete")

        self._state = ChannelBusyState.CLEAR
        return self._record(raw, "clear-release side sustained for full recent-RX hold")

    def _record(self, raw: int, reason: str) -> ChannelBusyDecision:
        decision = ChannelBusyDecision(
            state=self._state,
            raw_magnitude=raw,
            channel_busy=self._state is not ChannelBusyState.CLEAR,
            recent_rx=self._state in {ChannelBusyState.BUSY, ChannelBusyState.RECENT_RX},
            clear_candidate_since=self._clear_candidate_since,
            last_busy_at=self._last_busy_at,
            reason=reason,
        )
        self._last_decision = decision
        return decision
