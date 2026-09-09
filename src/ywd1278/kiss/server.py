"""RX-only TCP KISS service foundation.

This layer intentionally has no modem dependency and no transmit callback.
Inbound KISS DATA is counted and rejected at the backend boundary.  RX events
are published to connected clients through bounded per-client queues.
"""

from __future__ import annotations

from dataclasses import dataclass
from queue import Empty, Full, Queue
import socket
import socketserver
import threading

from .framing import DATA, KISSMessage, KISSStreamDecoder, encode


@dataclass(frozen=True)
class PacketEvent:
    frame_no_fcs: bytes
    source: str = ""
    destination: str = ""
    frame_type: str = ""


@dataclass(frozen=True)
class BackendSnapshot:
    stored_events: int
    subscribers: int
    tx_rejected: int
    control_ignored: int
    subscriber_drops: int


class RXOnlyBackend:
    """Thread-safe bounded RX event source with TX capability absent."""

    def __init__(
        self,
        events: tuple[PacketEvent, ...] | list[PacketEvent] = (),
        *,
        history_capacity: int = 256,
        subscriber_queue_capacity: int = 64,
    ) -> None:
        if history_capacity < 0:
            raise ValueError("history_capacity must be >= 0")
        if subscriber_queue_capacity < 1:
            raise ValueError("subscriber_queue_capacity must be >= 1")
        self._history_capacity = int(history_capacity)
        self._subscriber_queue_capacity = int(subscriber_queue_capacity)
        self._events = list(events)[-self._history_capacity :] if self._history_capacity else []
        self._subscribers: set[Queue[PacketEvent]] = set()
        self._lock = threading.Lock()
        self._tx_rejected = 0
        self._control_ignored = 0
        self._subscriber_drops = 0

    @property
    def snapshot(self) -> BackendSnapshot:
        with self._lock:
            return BackendSnapshot(
                stored_events=len(self._events),
                subscribers=len(self._subscribers),
                tx_rejected=self._tx_rejected,
                control_ignored=self._control_ignored,
                subscriber_drops=self._subscriber_drops,
            )

    def open_stream(self) -> tuple[list[PacketEvent], Queue[PacketEvent]]:
        queue: Queue[PacketEvent] = Queue(maxsize=self._subscriber_queue_capacity)
        with self._lock:
            self._subscribers.add(queue)
            history = list(self._events)
        return history, queue

    def close_stream(self, queue: Queue[PacketEvent]) -> None:
        with self._lock:
            self._subscribers.discard(queue)

    def publish(self, event: PacketEvent) -> None:
        with self._lock:
            if self._history_capacity:
                self._events.append(event)
                if len(self._events) > self._history_capacity:
                    del self._events[: len(self._events) - self._history_capacity]
            subscribers = tuple(self._subscribers)

        drops = 0
        for queue in subscribers:
            try:
                queue.put_nowait(event)
            except Full:
                drops += 1
        if drops:
            with self._lock:
                self._subscriber_drops += drops

    def reject_client_message(self, message: KISSMessage) -> None:
        with self._lock:
            if message.command == DATA:
                self._tx_rejected += 1
            else:
                self._control_ignored += 1


class ThreadingKISSServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        backend: RXOnlyBackend,
        *,
        socket_timeout: float = 0.05,
        verbose: bool = False,
    ) -> None:
        self.backend = backend
        self.socket_timeout = float(socket_timeout)
        self.verbose = bool(verbose)
        super().__init__(address, KISSHandler)


class KISSHandler(socketserver.BaseRequestHandler):
    server: ThreadingKISSServer

    def _emit(self, event: PacketEvent) -> bool:
        try:
            self.request.sendall(encode(event.frame_no_fcs, port=0, command=DATA))
            return True
        except (BrokenPipeError, ConnectionResetError, OSError):
            return False

    def handle(self) -> None:
        history, event_queue = self.server.backend.open_stream()
        decoder = KISSStreamDecoder()
        self.request.settimeout(self.server.socket_timeout)
        try:
            for event in history:
                if not self._emit(event):
                    return

            while True:
                while True:
                    try:
                        event = event_queue.get_nowait()
                    except Empty:
                        break
                    if not self._emit(event):
                        return

                try:
                    data = self.request.recv(4096)
                except socket.timeout:
                    continue
                except (ConnectionResetError, OSError):
                    break
                if not data:
                    break

                discarded_before = decoder.discarded_frames
                messages = decoder.feed(data)
                discarded = decoder.discarded_frames - discarded_before
                if discarded:
                    # 0C-P6's control-aware backend exposes this optional hook.
                    # Historical RXOnlyBackend behavior remains unchanged.
                    note_malformed = getattr(
                        self.server.backend,
                        "note_malformed_stream_frames",
                        None,
                    )
                    if callable(note_malformed):
                        note_malformed(discarded)

                for message in messages:
                    self.server.backend.reject_client_message(message)
        finally:
            self.server.backend.close_stream(event_queue)


def start_server_thread(
    backend: RXOnlyBackend,
    *,
    host: str = "127.0.0.1",
    port: int = 8001,
    verbose: bool = False,
) -> tuple[ThreadingKISSServer, threading.Thread]:
    """Start an RX-only KISS server in a daemon thread.

    ``port=0`` may be used by tests to request an ephemeral local port.
    """
    if not 0 <= port <= 65535:
        raise ValueError("KISS TCP port must be 0..65535")
    server = ThreadingKISSServer((host, port), backend, verbose=verbose)
    thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.05},
        name="ywd1278-kiss-server",
        daemon=True,
    )
    thread.start()
    return server, thread


def stop_server_thread(server: ThreadingKISSServer, thread: threading.Thread) -> None:
    server.shutdown()
    server.server_close()
    thread.join(timeout=2.0)
    if thread.is_alive():
        raise RuntimeError("KISS server thread did not stop")
