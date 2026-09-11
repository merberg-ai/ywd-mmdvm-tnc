"""In-memory read-only dashboard state and non-blocking SSE fan-out."""
from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
from queue import Full, Queue
import threading
import time
from typing import Any


@dataclass(frozen=True)
class LiveSnapshot:
    monitor_state: str
    monitor_generation: int
    monitor_connected_at: float | None
    last_event_monotonic: float | None
    last_event_ts: str | None
    last_seq: int | None
    events_received: int
    bad_json: int
    bad_schema: int
    reconnects: int
    sse_clients: int
    sse_drops: int
    recent_events: int
    event_counts: dict[str, int]


class LiveState:
    """Thread-safe state. Slow browsers can never block monitor ingestion."""

    def __init__(self, *, recent_capacity: int = 512, sse_queue_capacity: int = 256) -> None:
        self._recent = deque(maxlen=recent_capacity)
        self._sse_capacity = sse_queue_capacity
        self._subscribers: set[Queue[dict[str, Any]]] = set()
        self._counts: Counter[str] = Counter()
        self._lock = threading.Lock()
        self._monitor_state = "starting"
        self._generation = 0
        self._connected_at: float | None = None
        self._last_event_monotonic: float | None = None
        self._last_event_ts: str | None = None
        self._last_seq: int | None = None
        self._events_received = 0
        self._bad_json = 0
        self._bad_schema = 0
        self._reconnects = 0
        self._sse_drops = 0

    def set_connecting(self) -> None:
        with self._lock:
            self._monitor_state = "connecting"

    def set_connected(self) -> int:
        with self._lock:
            if self._generation:
                self._reconnects += 1
            self._generation += 1
            self._monitor_state = "connected"
            self._connected_at = time.time()
            return self._generation

    def set_disconnected(self) -> None:
        with self._lock:
            self._monitor_state = "reconnecting"
            self._connected_at = None

    def count_bad_json(self) -> None:
        with self._lock:
            self._bad_json += 1

    def count_bad_schema(self) -> None:
        with self._lock:
            self._bad_schema += 1

    def publish(self, event: dict[str, Any]) -> None:
        record = dict(event)
        with self._lock:
            record["_web_generation"] = self._generation
            self._events_received += 1
            self._last_event_monotonic = time.monotonic()
            self._last_event_ts = str(record.get("ts")) if record.get("ts") is not None else None
            seq = record.get("seq")
            self._last_seq = int(seq) if isinstance(seq, int) else None
            self._counts[str(record.get("event", "unknown"))] += 1
            self._recent.append(record)
            subscribers = tuple(self._subscribers)
        drops = 0
        for queue in subscribers:
            try:
                queue.put_nowait(record)
            except Full:
                drops += 1
        if drops:
            with self._lock:
                self._sse_drops += drops

    def subscribe(self) -> Queue[dict[str, Any]]:
        queue: Queue[dict[str, Any]] = Queue(maxsize=self._sse_capacity)
        with self._lock:
            self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: Queue[dict[str, Any]]) -> None:
        with self._lock:
            self._subscribers.discard(queue)

    def recent(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._recent]

    @property
    def snapshot(self) -> LiveSnapshot:
        with self._lock:
            return LiveSnapshot(
                monitor_state=self._monitor_state,
                monitor_generation=self._generation,
                monitor_connected_at=self._connected_at,
                last_event_monotonic=self._last_event_monotonic,
                last_event_ts=self._last_event_ts,
                last_seq=self._last_seq,
                events_received=self._events_received,
                bad_json=self._bad_json,
                bad_schema=self._bad_schema,
                reconnects=self._reconnects,
                sse_clients=len(self._subscribers),
                sse_drops=self._sse_drops,
                recent_events=len(self._recent),
                event_counts=dict(self._counts),
            )
