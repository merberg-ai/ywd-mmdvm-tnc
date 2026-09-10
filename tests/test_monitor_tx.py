from __future__ import annotations

import json
from queue import Empty
from types import SimpleNamespace
import unittest

from ywd1278.ax25 import Address, build_ui_frame
from ywd1278.kiss.tx_path import KISSDataRequestState
from ywdtnc.monitor import MonitorEventHub
from ywdtnc.monitor_tx import (
    MonitoredAdmissionQueue,
    MonitoredContextualLifecycle,
    MonitoredContextualRouter,
    TXRequestTracker,
)


def _drain(queue) -> list[dict]:  # type: ignore[no-untyped-def]
    records: list[dict] = []
    while True:
        try:
            payload = queue.get_nowait()
        except Empty:
            break
        records.append(json.loads(payload.decode("utf-8")))
    return records


def _frame() -> bytes:
    return build_ui_frame(
        source=Address("KJ6YWD", 11),
        destination=Address("KJ6YWD", 5),
        info=b"MONITOR TEST",
        include_fcs=False,
    )


class _RouterDelegate:
    snapshot = SimpleNamespace()

    def submit_frame(self, frame_with_fcs: bytes, context, *, timeout=None):  # type: ignore[no-untyped-def]
        _ = frame_with_fcs, context, timeout
        return SimpleNamespace(
            frame_sha256="abc123",
            selector_count=321,
            packed_selector_bytes=41,
            packed_selector_sha256="def456",
            nominal_duration_seconds=0.2675,
        )


class _FailingRouterDelegate(_RouterDelegate):
    def submit_frame(self, frame_with_fcs: bytes, context, *, timeout=None):  # type: ignore[no-untyped-def]
        _ = frame_with_fcs, context, timeout
        raise RuntimeError("synthetic downstream failure")


class _LifecycleDelegate:
    half_duplex_snapshot = SimpleNamespace()
    router_snapshot = SimpleNamespace()

    def __init__(self, router) -> None:  # type: ignore[no-untyped-def]
        self.router = router

    def submit_frame(self, frame_with_fcs: bytes, context, *, timeout=None):  # type: ignore[no-untyped-def]
        return self.router.submit_frame(frame_with_fcs, context, timeout=timeout)


class _AdmissionDelegate:
    request_timeout_seconds = 30.0
    snapshot = SimpleNamespace(queue_depth=0)

    def __init__(self, lifecycle, state=KISSDataRequestState.DISPATCHED) -> None:  # type: ignore[no-untyped-def]
        self.lifecycle = lifecycle
        self.state = state
        self.frame = b""
        self.context = None

    def enqueue(self, frame_no_fcs: bytes, context, *, now: float):  # type: ignore[no-untyped-def]
        _ = now
        self.frame = bytes(frame_no_fcs)
        self.context = context
        return SimpleNamespace(
            request_id=7,
            txdelay=30,
            persist=63,
            slottime=10,
            parameter_generation=2,
        )

    def observe_rssi(self, *, now: float, raw_magnitude: int, random_byte_source=None):  # type: ignore[no-untyped-def]
        _ = now, raw_magnitude, random_byte_source
        if self.state is KISSDataRequestState.TIMED_OUT:
            return SimpleNamespace(
                request_state=self.state,
                request_id=7,
                reason="synthetic bounded timeout",
            )
        try:
            self.lifecycle.submit_frame(self.frame + b"\x00\x00", self.context, timeout=1.5)
        except RuntimeError:
            return SimpleNamespace(
                request_state=KISSDataRequestState.DOWNSTREAM_FAILED,
                request_id=7,
                reason="synthetic downstream failure",
            )
        return SimpleNamespace(
            request_state=self.state,
            request_id=7,
            reason="synthetic dispatch",
        )


class MonitorTXTests(unittest.TestCase):
    def _chain(self, *, failing: bool = False, state=KISSDataRequestState.DISPATCHED):  # type: ignore[no-untyped-def]
        hub = MonitorEventHub(timestamp_factory=lambda: "2026-09-10T15:00:00.000Z")
        queue = hub.subscribe()
        tracker = TXRequestTracker()
        router_delegate = _FailingRouterDelegate() if failing else _RouterDelegate()
        router = MonitoredContextualRouter(router_delegate, hub, tracker)
        lifecycle = MonitoredContextualLifecycle(_LifecycleDelegate(router), hub, tracker)
        admission = MonitoredAdmissionQueue(_AdmissionDelegate(lifecycle, state), hub, tracker)
        return hub, queue, admission

    def test_successful_tx_lifecycle_has_exact_order_and_request_identity(self) -> None:
        _, queue, admission = self._chain()
        frame = _frame()
        admission.enqueue(frame, object(), now=1.0)
        observation = admission.observe_rssi(
            now=2.0,
            raw_magnitude=147,
            random_byte_source=lambda: 0,
        )
        self.assertIs(observation.request_state, KISSDataRequestState.DISPATCHED)

        records = _drain(queue)
        self.assertEqual(
            [record["event"] for record in records],
            ["tx.queued", "tx.channel_clear", "tx.dispatched", "tx.complete"],
        )
        self.assertTrue(all(record["request_id"] == 7 for record in records))
        self.assertTrue(all(record["source"] == "KJ6YWD-11" for record in records))
        self.assertTrue(all(record["destination"] == "KJ6YWD-5" for record in records))
        self.assertEqual(records[1]["raw_rssi"], 147)
        self.assertEqual(records[2]["selector_count"], 321)
        self.assertEqual(records[0]["txdelay"], 30)
        self.assertEqual(records[0]["persist"], 63)
        self.assertEqual(records[0]["slottime"], 10)

    def test_timeout_is_terminal_without_dispatch_or_complete(self) -> None:
        _, queue, admission = self._chain(state=KISSDataRequestState.TIMED_OUT)
        admission.enqueue(_frame(), object(), now=1.0)
        observation = admission.observe_rssi(now=31.0, raw_magnitude=190)
        self.assertIs(observation.request_state, KISSDataRequestState.TIMED_OUT)

        records = _drain(queue)
        self.assertEqual([record["event"] for record in records], ["tx.queued", "tx.timeout"])
        self.assertEqual(records[-1]["reason"], "synthetic bounded timeout")

    def test_downstream_failure_never_reports_dispatched_or_complete(self) -> None:
        _, queue, admission = self._chain(failing=True)
        admission.enqueue(_frame(), object(), now=1.0)
        observation = admission.observe_rssi(now=2.0, raw_magnitude=140)
        self.assertIs(observation.request_state, KISSDataRequestState.DOWNSTREAM_FAILED)

        records = _drain(queue)
        kinds = [record["event"] for record in records]
        self.assertEqual(kinds, ["tx.queued", "tx.channel_clear", "tx.failed"])
        self.assertIn("synthetic downstream failure", records[-1]["error"])
        self.assertNotIn("tx.dispatched", kinds)
        self.assertNotIn("tx.complete", kinds)


if __name__ == "__main__":
    unittest.main()
