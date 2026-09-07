"""Minimal streaming AGW network-protocol framing for raw AX.25 mode."""

from __future__ import annotations

from dataclasses import dataclass
import struct


HEADER = struct.Struct("<B3scBBB10s10sII")
HEADER_BYTES = HEADER.size
MAX_DATA_BYTES = 4096
RAW_MONITOR = b"k"
RAW_FRAME = b"K"


@dataclass(frozen=True)
class AGWHeader:
    port: int
    data_kind: bytes
    pid: int = 0
    call_from: bytes = b""
    call_to: bytes = b""
    data_length: int = 0
    reserved: int = 0


@dataclass(frozen=True)
class AGWFrame:
    header: AGWHeader
    data: bytes


def _call(value: bytes) -> bytes:
    value = bytes(value)
    if len(value) > 10:
        raise ValueError("AGW callsign field exceeds 10 bytes")
    return value.ljust(10, b"\x00")


def encode_frame(
    data_kind: bytes,
    data: bytes = b"",
    *,
    port: int = 0,
    pid: int = 0,
    call_from: bytes = b"",
    call_to: bytes = b"",
) -> bytes:
    data_kind = bytes(data_kind)
    data = bytes(data)
    if len(data_kind) != 1:
        raise ValueError("AGW DataKind must be exactly one byte")
    if not 0 <= port <= 255 or not 0 <= pid <= 255:
        raise ValueError("AGW port/PID must fit in one byte")
    if len(data) > MAX_DATA_BYTES:
        raise ValueError("AGW payload is too large")
    header = HEADER.pack(
        port,
        b"\x00\x00\x00",
        data_kind,
        0,
        pid,
        0,
        _call(call_from),
        _call(call_to),
        len(data),
        0,
    )
    return header + data


def decode_header(data: bytes) -> AGWHeader:
    if len(data) != HEADER_BYTES:
        raise ValueError(f"AGW header must be exactly {HEADER_BYTES} bytes")
    port, _, kind, _, pid, _, call_from, call_to, length, reserved = HEADER.unpack(data)
    if length > MAX_DATA_BYTES:
        raise ValueError("AGW payload length exceeds safety limit")
    return AGWHeader(
        port=port,
        data_kind=kind,
        pid=pid,
        call_from=call_from.rstrip(b"\x00"),
        call_to=call_to.rstrip(b"\x00"),
        data_length=length,
        reserved=reserved,
    )


class AGWStreamDecoder:
    """Decode split or coalesced AGW frames from a TCP byte stream."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[AGWFrame]:
        self._buffer.extend(data)
        frames: list[AGWFrame] = []
        while len(self._buffer) >= HEADER_BYTES:
            header = decode_header(bytes(self._buffer[:HEADER_BYTES]))
            total = HEADER_BYTES + header.data_length
            if len(self._buffer) < total:
                break
            payload = bytes(self._buffer[HEADER_BYTES:total])
            del self._buffer[:total]
            frames.append(AGWFrame(header, payload))
        return frames
