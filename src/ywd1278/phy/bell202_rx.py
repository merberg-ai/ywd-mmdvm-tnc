"""Incremental one-pass Bell-202 -> AX.25 receiver for YWD-1278.

Ported from the frozen YWD-MMDVM AX25-3C streaming receiver at
``d25180ad663d781b761c525d1e699e7b052d6214``
(`tools/packetd/streaming_rx.py`).

Packed one-bit slicer bytes are consumed exactly once. A precomputed exact
12-sample Bell-202 metric table and a persistent 1196..1204 baud x 16-phase
hypothesis bank carry timing, NRZI and HDLC state across feed() calls.

This module is RX-only DSP. It contains no UART ownership, GPIO access, RF
configuration or TX commands.
"""

from __future__ import annotations

from dataclasses import dataclass
import heapq
import math
from typing import Iterable

from ywd1278.ax25 import parse_frame, verify_fcs
from ywd1278.phy.bell202_tx import flag_bits, unstuff_bits

SAMPLE_RATE = 19_200.0
CORR_WINDOW = 12
MARK_HZ = 1200.0
SPACE_HZ = 2200.0
OCCURRENCE_TOLERANCE_SAMPLES = int(round(0.050 * SAMPLE_RATE))

# Frozen AX25-3C acquisition bank: 9 baud hypotheses x 16 whole-sample phases
# = 144 persistent hypotheses. Do not broaden this back into overlapping-window
# exhaustive search in the steady-state decoder.
DEFAULT_BAUDS = tuple(float(value) for value in range(1196, 1205))
DEFAULT_PHASES = tuple(float(value) for value in range(16))
MAX_STUFFED_BITS = 4096
HISTORY_SIZE = 128


@dataclass(frozen=True)
class StreamingFrame:
    frame: bytes
    sample_start: int
    sample_end: int
    baud: float
    phase: float
    method: str = "stream-hybrid"


@dataclass(frozen=True)
class StreamingStats:
    packed_bytes: int
    samples: int
    metric_windows: int
    symbol_decisions: int
    hypotheses: int
    flags_seen: int
    valid_frames: int
    duplicate_occurrences_suppressed: int
    max_frame_buffer_bits: int


@dataclass
class _Hypothesis:
    baud: float
    phase: float
    period: float
    next_symbol_end: float
    previous_selector: int | None = None
    shift: int = 0
    shift_count: int = 0
    after_flag: list[int] | None = None
    opening_data_sample: int | None = None
    flags_seen: int = 0


def _references(frequency: float) -> tuple[tuple[float, ...], tuple[float, ...]]:
    omega = 2.0 * math.pi * frequency / SAMPLE_RATE
    return (
        tuple(math.cos(omega * i) for i in range(CORR_WINDOW)),
        tuple(math.sin(omega * i) for i in range(CORR_WINDOW)),
    )


_MARK_C, _MARK_S = _references(MARK_HZ)
_SPACE_C, _SPACE_S = _references(SPACE_HZ)
_MARK_C_SUM = sum(_MARK_C)
_MARK_S_SUM = sum(_MARK_S)
_SPACE_C_SUM = sum(_SPACE_C)
_SPACE_S_SUM = sum(_SPACE_S)
_FLAG_BYTE = 0x7E
_WINDOW_MASK = (1 << CORR_WINDOW) - 1
_BYTE_BITS = tuple(
    tuple(1 if byte & (0x80 >> bit) else 0 for bit in range(8))
    for byte in range(256)
)


def _metric_for_pattern(pattern: int) -> float:
    """Exact normalized Bell-202 metric for one 12-bit slicer pattern."""

    values = tuple(
        1.0 if pattern & (1 << (CORR_WINDOW - 1 - index)) else -1.0
        for index in range(CORR_WINDOW)
    )
    mean = sum(values) / CORR_WINDOW
    mc = sum(raw * ref for raw, ref in zip(values, _MARK_C)) - mean * _MARK_C_SUM
    ms = sum(raw * ref for raw, ref in zip(values, _MARK_S)) - mean * _MARK_S_SUM
    sc = sum(raw * ref for raw, ref in zip(values, _SPACE_C)) - mean * _SPACE_C_SUM
    ss = sum(raw * ref for raw, ref in zip(values, _SPACE_S)) - mean * _SPACE_S_SUM
    denom = float(CORR_WINDOW * CORR_WINDOW)
    mark = (mc * mc + ms * ms) / denom
    space = (sc * sc + ss * ss) / denom
    total = mark + space
    return (space - mark) / total if total > 1e-9 else 0.0


# One slicer sample is one bit. A 12-sample correlation window therefore has
# only 4096 exact patterns. Precomputing them removes the old repeated Python
# correlation work without narrowing acquisition coverage.
_METRIC_TABLE = tuple(_metric_for_pattern(pattern) for pattern in range(1 << CORR_WINDOW))


def bits_to_bytes_lsb(bits: Iterable[int]) -> bytes:
    """Rebuild exact octets from an LSB-first HDLC data-bit sequence."""

    src = [1 if bit else 0 for bit in bits]
    if len(src) % 8:
        raise ValueError("bit count is not an exact number of octets")
    out = bytearray()
    for offset in range(0, len(src), 8):
        value = 0
        for bit in range(8):
            value |= src[offset + bit] << bit
        out.append(value)
    return bytes(out)


class StreamingBell202Decoder:
    """Stateful one-pass Bell-202/HDLC decoder.

    Physical samples are never rescanned. The complete 144-hypothesis default
    bank remains active, but a min-heap visits a hypothesis only when its next
    symbol boundary is due.
    """

    def __init__(
        self,
        *,
        bauds: tuple[float, ...] = DEFAULT_BAUDS,
        phases: tuple[float, ...] = DEFAULT_PHASES,
    ) -> None:
        if not bauds or not phases:
            raise ValueError("at least one baud and phase hypothesis is required")
        if any(value <= 0.0 for value in bauds):
            raise ValueError("baud hypotheses must be positive")

        self._hypotheses: list[_Hypothesis] = []
        self._schedule: list[tuple[int, int, _Hypothesis]] = []
        order = 0
        for baud in bauds:
            period = SAMPLE_RATE / baud
            for phase in phases:
                hyp = _Hypothesis(
                    baud=baud,
                    phase=phase,
                    period=period,
                    next_symbol_end=phase + period,
                )
                self._hypotheses.append(hyp)
                heapq.heappush(
                    self._schedule,
                    (int(round(hyp.next_symbol_end)), order, hyp),
                )
                order += 1

        self._sample_index = -1
        self._packed_bytes = 0
        self._metric_windows = 0
        self._symbol_decisions = 0
        self._flags_seen = 0
        self._valid_frames = 0
        self._duplicates = 0
        self._max_frame_buffer_bits = 0
        self._occurrences: list[StreamingFrame] = []

        self._metric_register = 0
        self._metric_sample_count = 0
        self._metric_tags = [-1] * HISTORY_SIZE
        self._metric_values = [0.0] * HISTORY_SIZE
        self._transition_tags = [-1] * HISTORY_SIZE
        self._transition_prefix = [0] * HISTORY_SIZE
        self._transitions_total = 0
        self._previous_raw: int | None = None

    def _store_transition_prefix(self, prefix_index: int) -> None:
        slot = prefix_index % HISTORY_SIZE
        self._transition_tags[slot] = prefix_index
        self._transition_prefix[slot] = self._transitions_total

    def _transition_value(self, prefix_index: int) -> int | None:
        slot = prefix_index % HISTORY_SIZE
        if self._transition_tags[slot] != prefix_index:
            return None
        return self._transition_prefix[slot]

    def _transition_count(self, start: int, end: int) -> int:
        # Adjacent transitions wholly inside [start, end), matching the frozen
        # AX25-3C classifier semantics.
        if end - start < 2:
            return 0
        left = self._transition_value(start + 1)
        right = self._transition_value(end)
        if left is None or right is None:
            return 0
        return max(0, right - left)

    def _store_metric(self, raw: int) -> None:
        self._metric_register = ((self._metric_register << 1) | raw) & _WINDOW_MASK
        self._metric_sample_count += 1
        if self._metric_sample_count < CORR_WINDOW:
            return
        start = self._sample_index - CORR_WINDOW + 1
        slot = start % HISTORY_SIZE
        self._metric_tags[slot] = start
        self._metric_values[slot] = _METRIC_TABLE[self._metric_register]
        self._metric_windows += 1

    def _metric_at(self, start: int) -> float | None:
        slot = start % HISTORY_SIZE
        if self._metric_tags[slot] != start:
            return None
        return self._metric_values[slot]

    @staticmethod
    def _same_occurrence(left: StreamingFrame, right: StreamingFrame) -> bool:
        return (
            left.frame == right.frame
            and abs(left.sample_start - right.sample_start) <= OCCURRENCE_TOLERANCE_SAMPLES
        )

    def _accept_frame(self, item: StreamingFrame, fresh: list[StreamingFrame]) -> None:
        duplicate_index = next(
            (
                index
                for index, existing in enumerate(self._occurrences)
                if self._same_occurrence(existing, item)
            ),
            None,
        )
        if duplicate_index is None:
            self._occurrences.append(item)
            fresh.append(item)
            self._valid_frames += 1
        else:
            self._duplicates += 1
            # Prefer the earliest occurrence timestamp across timing hypotheses.
            if item.sample_start < self._occurrences[duplicate_index].sample_start:
                self._occurrences[duplicate_index] = item

    def _finish_flag(
        self,
        hyp: _Hypothesis,
        bit_sample: int,
        fresh: list[StreamingFrame],
    ) -> None:
        hyp.flags_seen += 1
        self._flags_seen += 1

        if hyp.after_flag is not None:
            stuffed = hyp.after_flag[:-8] if len(hyp.after_flag) >= 8 else []
            if stuffed and hyp.opening_data_sample is not None:
                try:
                    payload_bits = unstuff_bits(stuffed)
                    frame = bits_to_bytes_lsb(payload_bits)
                except ValueError:
                    frame = b""
                if len(frame) >= 17 and verify_fcs(frame):
                    try:
                        parse_frame(frame, has_fcs=True)
                    except ValueError:
                        pass
                    else:
                        closing_flag_start = int(round(bit_sample - 7.0 * hyp.period))
                        self._accept_frame(
                            StreamingFrame(
                                frame=frame,
                                sample_start=hyp.opening_data_sample,
                                sample_end=closing_flag_start,
                                baud=hyp.baud,
                                phase=hyp.phase,
                            ),
                            fresh,
                        )

        # Current flag becomes the opening flag for the next candidate frame.
        hyp.after_flag = []
        hyp.opening_data_sample = int(round(bit_sample + hyp.period))

    def _emit_bit(
        self,
        hyp: _Hypothesis,
        bit: int,
        bit_sample: int,
        fresh: list[StreamingFrame],
    ) -> None:
        hyp.shift = ((hyp.shift << 1) | bit) & 0xFF
        if hyp.shift_count < 8:
            hyp.shift_count += 1

        if hyp.after_flag is not None:
            hyp.after_flag.append(bit)
            self._max_frame_buffer_bits = max(self._max_frame_buffer_bits, len(hyp.after_flag))
            if len(hyp.after_flag) > MAX_STUFFED_BITS:
                hyp.after_flag = None
                hyp.opening_data_sample = None

        if hyp.shift_count >= 8 and hyp.shift == _FLAG_BYTE:
            self._finish_flag(hyp, bit_sample, fresh)

    def _classify_hypothesis(
        self,
        hyp: _Hypothesis,
        fresh: list[StreamingFrame],
    ) -> None:
        boundary = int(round(hyp.next_symbol_end - hyp.period))
        symbol_end = int(round(hyp.next_symbol_end))
        centered = int(round(boundary + (hyp.period - CORR_WINDOW) * 0.5))
        norm = self._metric_at(centered)
        if norm is not None:
            corr_selector = 1 if norm > 0.0 else 0
            zc = self._transition_count(boundary, symbol_end)
            zc_selector = 1 if zc >= 3 else 0
            selector = corr_selector if abs(norm) >= 0.12 else zc_selector
            if hyp.previous_selector is None:
                bit = 1
            else:
                bit = 1 if selector == hyp.previous_selector else 0
            hyp.previous_selector = selector
            self._emit_bit(hyp, bit, max(0, symbol_end - 1), fresh)
            self._symbol_decisions += 1

    def _classify_due_symbols(self, fresh: list[StreamingFrame]) -> None:
        # The min-heap preserves all hypotheses but avoids testing all 144 on
        # every 19.2ksps sample. There is no overlapping-window rescan queue.
        current_end = self._sample_index + 1
        while self._schedule and self._schedule[0][0] <= current_end:
            _, order, hyp = heapq.heappop(self._schedule)
            self._classify_hypothesis(hyp, fresh)
            hyp.next_symbol_end += hyp.period
            heapq.heappush(
                self._schedule,
                (int(round(hyp.next_symbol_end)), order, hyp),
            )

    def _feed_sample(self, raw: int, fresh: list[StreamingFrame]) -> None:
        self._sample_index += 1
        if self._previous_raw is not None and raw != self._previous_raw:
            self._transitions_total += 1
        self._previous_raw = raw
        self._store_transition_prefix(self._sample_index + 1)
        self._store_metric(raw)
        self._classify_due_symbols(fresh)

    def feed(self, packed: bytes) -> list[StreamingFrame]:
        """Consume packed slicer bytes and return newly found frame occurrences."""

        fresh: list[StreamingFrame] = []
        self._packed_bytes += len(packed)
        for byte in packed:
            for raw in _BYTE_BITS[byte]:
                self._feed_sample(raw, fresh)
        fresh.sort(key=lambda item: item.sample_start)
        return fresh

    def finish(self) -> list[StreamingFrame]:
        """Finish the stream; no queued DSP drain exists in this architecture."""

        return []

    @property
    def occurrences(self) -> tuple[StreamingFrame, ...]:
        return tuple(sorted(self._occurrences, key=lambda item: item.sample_start))

    @property
    def stats(self) -> StreamingStats:
        return StreamingStats(
            packed_bytes=self._packed_bytes,
            samples=self._sample_index + 1,
            metric_windows=self._metric_windows,
            symbol_decisions=self._symbol_decisions,
            hypotheses=len(self._hypotheses),
            flags_seen=self._flags_seen,
            valid_frames=self._valid_frames,
            duplicate_occurrences_suppressed=self._duplicates,
            max_frame_buffer_bits=self._max_frame_buffer_bits,
        )
