"""Bell-202/AX.25 HDLC TX serialization primitives for YWD-1278.

Ported from the frozen YWD-MMDVM packet foundation at
``d25180ad663d781b761c525d1e699e7b052d6214`` (`tools/ax25/afsk1200.py`).

This module stays entirely on the Raspberry Pi. It takes an AX.25 frame that
already includes FCS, serializes it as HDLC (LSB first plus bit stuffing),
applies AX.25 NRZI, and represents Bell-202 tone choice as one selector per
1200-baud symbol:

    0 = MARK  (1200 Hz)
    1 = SPACE (2200 Hz)

It does not open the modem UART, touch GPIO, key RF, or expand selectors into
waveform samples. Those operations belong to later qualified boundaries.
"""

from __future__ import annotations

from typing import Iterable, Sequence

FLAG = 0x7E
MARK = 0
SPACE = 1


def byte_bits_lsb(data: bytes) -> list[int]:
    """Return every byte LSB-first, as required by AX.25 HDLC."""

    out: list[int] = []
    for byte in data:
        out.extend((byte >> bit) & 1 for bit in range(8))
    return out


def flag_bits() -> list[int]:
    """Return the LSB-first bit representation of HDLC flag 0x7E."""

    return byte_bits_lsb(bytes([FLAG]))


def stuff_bits(bits: Iterable[int]) -> list[int]:
    """Insert a zero after every run of five consecutive one bits."""

    out: list[int] = []
    ones = 0
    for value in bits:
        bit = 1 if value else 0
        out.append(bit)
        if bit:
            ones += 1
            if ones == 5:
                out.append(0)
                ones = 0
        else:
            ones = 0
    return out


def unstuff_bits(bits: Iterable[int]) -> list[int]:
    """Reverse AX.25 HDLC bit stuffing and reject invalid stuffed streams."""

    src = [1 if value else 0 for value in bits]
    out: list[int] = []
    ones = 0
    i = 0
    while i < len(src):
        bit = src[i]
        out.append(bit)
        i += 1
        if bit:
            ones += 1
            if ones == 5:
                if i >= len(src) or src[i] != 0:
                    raise ValueError("invalid HDLC bit stuffing")
                i += 1
                ones = 0
        else:
            ones = 0
    return out


def hdlc_bits(
    frame_with_fcs: bytes,
    *,
    pre_flags: int = 45,
    post_flags: int = 3,
) -> list[int]:
    """Serialize one AX.25 frame as a complete HDLC burst.

    At 1200 baud, 45 opening flags are exactly 300 ms of flag preamble. Flags
    themselves are never bit-stuffed; only the frame body is stuffed.
    """

    if not frame_with_fcs:
        raise ValueError("AX.25 frame cannot be empty")
    if pre_flags < 1 or post_flags < 1:
        raise ValueError("at least one opening and closing flag is required")

    flag = flag_bits()
    return flag * pre_flags + stuff_bits(byte_bits_lsb(frame_with_fcs)) + flag * post_flags


def nrzi_encode(bits: Iterable[int], *, initial_tone: int = MARK) -> list[int]:
    """Convert AX.25 data bits to Bell-202 tone selectors using NRZI.

    AX.25 NRZI semantics are zero = change state, one = hold state.
    """

    if initial_tone not in (MARK, SPACE):
        raise ValueError("initial_tone must be MARK or SPACE")
    tone = initial_tone
    out: list[int] = []
    for value in bits:
        bit = 1 if value else 0
        if bit == 0:
            tone ^= 1
        out.append(tone)
    return out


def nrzi_decode(selectors: Iterable[int], *, initial_tone: int = MARK) -> list[int]:
    """Reference inverse of :func:`nrzi_encode` for deterministic tests."""

    if initial_tone not in (MARK, SPACE):
        raise ValueError("initial_tone must be MARK or SPACE")
    previous = initial_tone
    out: list[int] = []
    for value in selectors:
        tone = 1 if value else 0
        out.append(1 if tone == previous else 0)
        previous = tone
    return out


def pack_selectors(selectors: Sequence[int]) -> bytes:
    """Pack tone selectors MSB-first, matching the qualified YWD UART form."""

    out = bytearray((len(selectors) + 7) // 8)
    for i, value in enumerate(selectors):
        if value:
            out[i >> 3] |= 0x80 >> (i & 7)
    return bytes(out)


def unpack_selectors(packed: bytes, bit_count: int) -> list[int]:
    """Unpack the qualified selector wire representation."""

    if bit_count < 0 or bit_count > len(packed) * 8:
        raise ValueError("invalid selector bit count")
    return [
        1 if packed[i >> 3] & (0x80 >> (i & 7)) else 0
        for i in range(bit_count)
    ]


def frame_to_selectors(
    frame_with_fcs: bytes,
    *,
    pre_flags: int = 45,
    post_flags: int = 3,
    initial_tone: int = MARK,
) -> list[int]:
    """Convert one FCS-bearing AX.25 frame into 1200-baud tone selectors."""

    return nrzi_encode(
        hdlc_bits(frame_with_fcs, pre_flags=pre_flags, post_flags=post_flags),
        initial_tone=initial_tone,
    )


def duration_seconds(selector_count: int) -> float:
    """Return nominal Bell-202 burst duration for 1200-baud selectors."""

    return selector_count / 1200.0
