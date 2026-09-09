"""Private POSIX serial transport for the single modem owner.

The class is intentionally private.  Callers receive a factory closure and the
factory is expected to be executed by :class:`ywd1278.modem.owner.ModemOwner`.
The transport binds itself to its construction thread and refuses transact or
close calls from any other thread.
"""

from __future__ import annotations

import os
import select
import termios
import threading
import time
from typing import Callable

from . import protocol
from .owner import ModemTransport


class _PosixSerialTransport:
    def __init__(self, device: str, *, baud: int = 115200) -> None:
        if baud != 115200:
            raise ValueError("YWD-1278 modem transport currently requires 115200 baud")
        self._owner_thread_id = threading.get_ident()
        self._device = device
        self._fd = os.open(device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            self._configure()
        except BaseException:
            os.close(self._fd)
            self._fd = -1
            raise

    @property
    def device(self) -> str:
        return self._device

    def _assert_owner(self) -> None:
        if threading.get_ident() != self._owner_thread_id:
            raise RuntimeError("POSIX modem transport accessed outside its owner thread")
        if self._fd < 0:
            raise RuntimeError("POSIX modem transport is closed")

    def _configure(self) -> None:
        attrs = termios.tcgetattr(self._fd)
        attrs[0] = 0
        attrs[1] = 0
        attrs[2] = termios.CS8 | termios.CLOCAL | termios.CREAD
        attrs[3] = 0
        attrs[4] = termios.B115200
        attrs[5] = termios.B115200
        attrs[6][termios.VMIN] = 0
        attrs[6][termios.VTIME] = 0
        termios.tcsetattr(self._fd, termios.TCSANOW, attrs)
        termios.tcflush(self._fd, termios.TCIOFLUSH)

    def transact(self, request: bytes, *, timeout: float) -> bytes:
        self._assert_owner()
        if timeout <= 0.0:
            raise ValueError("serial transaction timeout must be positive")
        # Reject malformed outgoing frames before any device write occurs.
        protocol.parse_frame(request)
        termios.tcflush(self._fd, termios.TCIFLUSH)
        self._write_all(request, timeout=timeout)
        return self._read_frame(timeout=timeout)

    def _write_all(self, data: bytes, *, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        offset = 0
        while offset < len(data):
            now = time.monotonic()
            if now >= deadline:
                raise TimeoutError("timed out writing MMDVM host frame")
            try:
                written = os.write(self._fd, data[offset:])
            except BlockingIOError:
                written = 0
            if written:
                offset += written
                continue
            select.select([], [self._fd], [], min(0.05, deadline - now))

    def _read_frame(self, *, timeout: float) -> bytes:
        deadline = time.monotonic() + timeout
        data = bytearray()
        target: int | None = None

        while time.monotonic() < deadline:
            remaining = max(0.0, deadline - time.monotonic())
            ready, _, _ = select.select([self._fd], [], [], min(0.05, remaining))
            if self._fd not in ready:
                continue
            try:
                chunk = os.read(self._fd, 512)
            except BlockingIOError:
                continue
            if not chunk:
                continue

            for byte in chunk:
                if not data:
                    if byte != protocol.START:
                        continue
                    data.append(byte)
                    continue

                data.append(byte)
                if len(data) == 2:
                    target = data[1]
                    if target < 3:
                        # Invalid candidate start; resynchronize instead of
                        # handing malformed framing to higher layers.
                        data.clear()
                        target = None
                        continue
                if target is not None and len(data) >= target:
                    frame = bytes(data[:target])
                    protocol.parse_frame(frame)
                    return frame

        partial = data.hex(" ") if data else "<none>"
        raise TimeoutError(f"timed out waiting for MMDVM response; partial={partial}")

    def close(self) -> None:
        self._assert_owner()
        fd = self._fd
        self._fd = -1
        os.close(fd)


def posix_serial_transport_factory(
    device: str,
    *,
    baud: int = 115200,
) -> Callable[[], ModemTransport]:
    """Return a factory whose invocation opens and binds the serial device.

    The closure itself performs no I/O.  `ModemOwner` invokes it from inside
    the dedicated owner thread.
    """

    def factory() -> ModemTransport:
        return _PosixSerialTransport(device, baud=baud)

    return factory
