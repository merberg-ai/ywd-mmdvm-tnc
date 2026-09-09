"""Host-side physical-layer primitives for YWD-1278."""

from .bell202_rx import (
    CORR_WINDOW,
    DEFAULT_BAUDS,
    DEFAULT_PHASES,
    SAMPLE_RATE,
    StreamingBell202Decoder,
    StreamingFrame,
    StreamingStats,
)
from .bell202_tx import (
    MARK,
    SPACE,
    byte_bits_lsb,
    duration_seconds,
    flag_bits,
    frame_to_selectors,
    hdlc_bits,
    nrzi_decode,
    nrzi_encode,
    pack_selectors,
    stuff_bits,
    unpack_selectors,
    unstuff_bits,
)

__all__ = [
    "CORR_WINDOW",
    "DEFAULT_BAUDS",
    "DEFAULT_PHASES",
    "MARK",
    "SAMPLE_RATE",
    "SPACE",
    "StreamingBell202Decoder",
    "StreamingFrame",
    "StreamingStats",
    "byte_bits_lsb",
    "duration_seconds",
    "flag_bits",
    "frame_to_selectors",
    "hdlc_bits",
    "nrzi_decode",
    "nrzi_encode",
    "pack_selectors",
    "stuff_bits",
    "unpack_selectors",
    "unstuff_bits",
]
