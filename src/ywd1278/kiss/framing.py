"""Standard KISS framing and incremental stream decoding.

Ported from the frozen YWD-MMDVM packet boundary.  This module is pure bytes:
it has no socket, UART, modem, or RF dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass

FEND = 0xC0
FESC = 0xDB
TFEND = 0xDC
TFESC = 0xDD

# KISS low-nibble command identifiers.  0C-P6 handles only the classic runtime
# parameters named below; TXTAIL/SETHARDWARE/RETURN remain unsupported commands.
DATA = 0x00
TXDELAY = 0x01
PERSIST = 0x02
SLOTTIME = 0x03
TXTAIL = 0x04
FULLDUPLEX = 0x05
SETHARDWARE = 0x06
RETURN = 0x0F


@dataclass(frozen=True)
class KISSMessage:
    port: int
    command: int
    frame: bytes


def encode(frame: bytes, *, port: int = 0, command: int = DATA) -> bytes:
    if not 0 <= port <= 15:
        raise ValueError("KISS port must be 0..15")
    if not 0 <= command <= 15:
        raise ValueError("KISS command must be 0..15")

    body = bytes(((port << 4) | command,)) + bytes(frame)
    escaped = bytearray()
    for byte in body:
        if byte == FEND:
            escaped.extend((FESC, TFEND))
        elif byte == FESC:
            escaped.extend((FESC, TFESC))
        else:
            escaped.append(byte)
    return bytes((FEND,)) + bytes(escaped) + bytes((FEND,))


def decode(packet: bytes) -> KISSMessage:
    if len(packet) < 3 or packet[0] != FEND or packet[-1] != FEND:
        raise ValueError("KISS frame must start and end with FEND")

    raw = bytearray()
    escaped = False
    for byte in packet[1:-1]:
        if escaped:
            if byte == TFEND:
                raw.append(FEND)
            elif byte == TFESC:
                raw.append(FESC)
            else:
                raise ValueError("invalid KISS escape sequence")
            escaped = False
        elif byte == FESC:
            escaped = True
        elif byte == FEND:
            raise ValueError("unexpected FEND inside KISS frame")
        else:
            raw.append(byte)

    if escaped or not raw:
        raise ValueError("truncated KISS frame")

    type_byte = raw[0]
    return KISSMessage(
        port=(type_byte >> 4) & 0x0F,
        command=type_byte & 0x0F,
        frame=bytes(raw[1:]),
    )


class KISSStreamDecoder:
    """Incrementally decode KISS messages from arbitrary TCP/PTY chunks.

    Consecutive FEND bytes are harmless separators.  Invalid escape sequences
    discard only the current frame and resynchronize at the next FEND.

    ``max_body_bytes`` bounds memory used by a malformed peer that never emits
    a terminating FEND.  The limit includes the KISS type byte.
    """

    def __init__(self, *, max_body_bytes: int = 4096) -> None:
        if max_body_bytes < 1:
            raise ValueError("max_body_bytes must be positive")
        self._max_body_bytes = int(max_body_bytes)
        self._raw = bytearray()
        self._escaped = False
        self._discard = False
        self.discarded_frames = 0

    def reset(self) -> None:
        self._raw.clear()
        self._escaped = False
        self._discard = False

    def _append(self, byte: int) -> None:
        if len(self._raw) >= self._max_body_bytes:
            self._discard = True
            self.discarded_frames += 1
            return
        self._raw.append(byte)

    def feed(self, data: bytes) -> list[KISSMessage]:
        out: list[KISSMessage] = []
        for byte in data:
            if byte == FEND:
                if self._raw and not self._discard and not self._escaped:
                    type_byte = self._raw[0]
                    out.append(
                        KISSMessage(
                            port=(type_byte >> 4) & 0x0F,
                            command=type_byte & 0x0F,
                            frame=bytes(self._raw[1:]),
                        )
                    )
                elif self._escaped and not self._discard:
                    self.discarded_frames += 1
                self.reset()
                continue

            if self._discard:
                continue

            if self._escaped:
                if byte == TFEND:
                    self._append(FEND)
                elif byte == TFESC:
                    self._append(FESC)
                else:
                    self._discard = True
                    self.discarded_frames += 1
                self._escaped = False
                continue

            if byte == FESC:
                self._escaped = True
            else:
                self._append(byte)
        return out
