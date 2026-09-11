"""Side-band TX lifecycle instrumentation over the frozen qualified path."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import threading

from ywd1278.kiss.tx_path import KISSDataRequestState

from .monitor import MonitorEventHub, frame_fields


@dataclass(frozen=True)
class TrackedTXRequest:
    request_id: int
    frame: dict[str, object]
    txdelay: int
    persist: int
    slottime: int
    parameter_generation: int
    raw_rssi: int | None = None


class TXRequestTracker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: deque[int] = deque()
        self._records: dict[int, TrackedTXRequest] = {}
        self._current_id: int | None = None

    def add(self, receipt, frame_no_fcs: bytes) -> TrackedTXRequest:  # type: ignore[no-untyped-def]
        record = TrackedTXRequest(
            request_id=int(receipt.request_id),
            frame=frame_fields(frame_no_fcs),
            txdelay=int(receipt.txdelay),
            persist=int(receipt.persist),
            slottime=int(receipt.slottime),
            parameter_generation=int(receipt.parameter_generation),
        )
        with self._lock:
            self._pending.append(record.request_id)
            self._records[record.request_id] = record
        return record

    def begin_observation(self, raw_rssi: int) -> TrackedTXRequest | None:
        with self._lock:
            if not self._pending:
                self._current_id = None
                return None
            request_id = self._pending[0]
            old = self._records[request_id]
            record = TrackedTXRequest(
                old.request_id, old.frame, old.txdelay, old.persist, old.slottime,
                old.parameter_generation, int(raw_rssi)
            )
            self._records[request_id] = record
            self._current_id = request_id
            return record

    def current(self) -> TrackedTXRequest | None:
        with self._lock:
            return None if self._current_id is None else self._records.get(self._current_id)

    def clear_current(self) -> None:
        with self._lock:
            self._current_id = None

    def finish(self, request_id: int | None) -> None:
        if request_id is None:
            return
        with self._lock:
            try:
                self._pending.remove(int(request_id))
            except ValueError:
                pass
            self._records.pop(int(request_id), None)
            if self._current_id == int(request_id):
                self._current_id = None


def request_fields(record: TrackedTXRequest | None) -> dict[str, object]:
    if record is None:
        return {}
    fields = dict(record.frame)
    fields.update({
        "request_id": record.request_id,
        "txdelay": record.txdelay,
        "persist": record.persist,
        "slottime": record.slottime,
        "parameter_generation": record.parameter_generation,
    })
    if record.raw_rssi is not None:
        fields["raw_rssi"] = record.raw_rssi
    return fields


class MonitoredAdmissionQueue:
    def __init__(self, delegate, hub: MonitorEventHub, tracker: TXRequestTracker) -> None:  # type: ignore[no-untyped-def]
        self._delegate = delegate
        self._hub = hub
        self._tracker = tracker

    @property
    def request_timeout_seconds(self) -> float:
        return self._delegate.request_timeout_seconds

    @property
    def snapshot(self):  # type: ignore[no-untyped-def]
        return self._delegate.snapshot

    def enqueue(self, frame_no_fcs: bytes, context, *, now: float):  # type: ignore[no-untyped-def]
        receipt = self._delegate.enqueue(frame_no_fcs, context, now=now)
        record = self._tracker.add(receipt, frame_no_fcs)
        self._hub.publish("tx.queued", **request_fields(record))
        return receipt

    def observe_rssi(self, *, now: float, raw_magnitude: int, random_byte_source=None):  # type: ignore[no-untyped-def]
        record = self._tracker.begin_observation(raw_magnitude)
        try:
            observation = self._delegate.observe_rssi(
                now=now, raw_magnitude=raw_magnitude, random_byte_source=random_byte_source
            )
            if observation.request_state is KISSDataRequestState.TIMED_OUT:
                self._hub.publish("tx.timeout", **request_fields(record), reason=observation.reason)
            if observation.request_state in {
                KISSDataRequestState.DISPATCHED,
                KISSDataRequestState.TIMED_OUT,
                KISSDataRequestState.DOWNSTREAM_FAILED,
            }:
                self._tracker.finish(observation.request_id)
            return observation
        finally:
            self._tracker.clear_current()


class MonitoredContextualRouter:
    def __init__(self, delegate, hub: MonitorEventHub, tracker: TXRequestTracker) -> None:  # type: ignore[no-untyped-def]
        self._delegate = delegate
        self._hub = hub
        self._tracker = tracker

    @property
    def snapshot(self):  # type: ignore[no-untyped-def]
        return self._delegate.snapshot

    def submit_frame(self, frame_with_fcs: bytes, context, *, timeout: float | None = None):  # type: ignore[no-untyped-def]
        receipt = self._delegate.submit_frame(frame_with_fcs, context, timeout=timeout)
        fields = request_fields(self._tracker.current())
        for name in ("frame_sha256", "selector_count", "packed_selector_bytes", "packed_selector_sha256", "nominal_duration_seconds"):
            value = getattr(receipt, name, None)
            if value is not None:
                fields[name] = value
        self._hub.publish("tx.dispatched", **fields)
        return receipt


class MonitoredContextualLifecycle:
    def __init__(self, delegate, hub: MonitorEventHub, tracker: TXRequestTracker) -> None:  # type: ignore[no-untyped-def]
        self._delegate = delegate
        self._hub = hub
        self._tracker = tracker

    @property
    def half_duplex_snapshot(self):  # type: ignore[no-untyped-def]
        return self._delegate.half_duplex_snapshot

    @property
    def router_snapshot(self):  # type: ignore[no-untyped-def]
        return self._delegate.router_snapshot

    def submit_frame(self, frame_with_fcs: bytes, context, *, timeout: float | None = None):  # type: ignore[no-untyped-def]
        record = self._tracker.current()
        self._hub.publish("tx.channel_clear", **request_fields(record))
        try:
            result = self._delegate.submit_frame(frame_with_fcs, context, timeout=timeout)
        except BaseException as exc:
            self._hub.publish("tx.failed", **request_fields(record), error=f"{type(exc).__name__}: {exc}")
            raise
        self._hub.publish("tx.complete", **request_fields(record))
        return result
