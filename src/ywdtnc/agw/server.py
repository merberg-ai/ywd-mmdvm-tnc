"""Threaded AGW raw-mode server sharing the qualified YWD KISS backend."""

from __future__ import annotations

from queue import Empty, Queue
import socket
import socketserver
import threading

from ywd1278.kiss.framing import DATA, KISSMessage
from ywd1278.kiss.server import PacketEvent

from .framing import AGWStreamDecoder, RAW_FRAME, RAW_MONITOR, encode_frame


class ThreadingAGWServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address: tuple[str, int], backend, *, socket_timeout: float = 0.05) -> None:  # type: ignore[no-untyped-def]
        self.backend = backend
        self.socket_timeout = float(socket_timeout)
        super().__init__(address, AGWHandler)


class AGWHandler(socketserver.BaseRequestHandler):
    server: ThreadingAGWServer

    def _emit(self, event: PacketEvent) -> bool:
        # AGW raw K payloads carry a leading control/channel byte. BPQtoAGW
        # discards this byte and treats the remainder as the AX.25 frame body.
        try:
            self.request.sendall(encode_frame(RAW_FRAME, b"\x00" + event.frame_no_fcs, port=0))
            return True
        except (BrokenPipeError, ConnectionResetError, OSError):
            return False

    def handle(self) -> None:
        decoder = AGWStreamDecoder()
        event_queue: Queue[PacketEvent] | None = None
        self.request.settimeout(self.server.socket_timeout)
        try:
            while True:
                if event_queue is not None:
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

                try:
                    frames = decoder.feed(data)
                except ValueError:
                    break

                for frame in frames:
                    if frame.header.port != 0:
                        continue
                    if frame.header.data_kind == RAW_MONITOR:
                        if event_queue is None:
                            history, event_queue = self.server.backend.open_stream()
                            # ywd-tncd constructs its backend with history_capacity=0.
                            # Discard defensively if a future backend violates that contract.
                            history.clear()
                        continue
                    if frame.header.data_kind == RAW_FRAME:
                        if not frame.data:
                            continue
                        self.server.backend.reject_client_message(
                            KISSMessage(port=0, command=DATA, frame=frame.data[1:])
                        )
        finally:
            if event_queue is not None:
                self.server.backend.close_stream(event_queue)


def start_agw_server_thread(
    backend,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> tuple[ThreadingAGWServer, threading.Thread]:  # type: ignore[no-untyped-def]
    if not 0 <= port <= 65535:
        raise ValueError("AGW TCP port must be 0..65535")
    server = ThreadingAGWServer((host, port), backend)
    thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.05},
        name="ywd-tncd-agw",
        daemon=True,
    )
    thread.start()
    return server, thread


def stop_agw_server_thread(server: ThreadingAGWServer, thread: threading.Thread) -> None:
    server.shutdown()
    server.server_close()
    thread.join(timeout=2.0)
    if thread.is_alive():
        raise RuntimeError("AGW server thread did not stop")
