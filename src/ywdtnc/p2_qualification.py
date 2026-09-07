"""P2 one-shot physical TCP-KISS TX qualification for YWD-MMDVM-TNC.

This is a qualification harness around the normal product TNCEngine. It owns no
modem transport implementation, scheduler, retry loop, or alternate RF path.
It starts the exact product engine with an in-memory TX-enabled copy of an
otherwise TX-disabled qualified config, connects through the real TCP KISS
listener, emits exactly one port-0 DATA frame, and then proves same-connection
live RX recovery using the frozen runtime accounting.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
import socket
import sys
import time

from ywd1278.ax25.codec import Address, build_ui_frame, parse_frame
from ywd1278.kiss.framing import DATA, KISSStreamDecoder, encode

from . import QUALIFIED_TX_FREQUENCY_HZ, QUALIFIED_TX_POWER
from .config import TNCConfig, load_config, validate_config
from .engine import TNCEngine


AUTHORIZATION = "P2-TX-ONCE-145050"
EXTERNAL_CONFIRMATION = "EXTERNAL-DECODE-MATCH-ONE"
DEFAULT_SOURCE = "KJ6YWD-10"
DEFAULT_DESTINATION = "YWD127"
DEFAULT_INFORMATION = "YWD-MMDVM-TNC P2 1/1"


class P2QualificationError(RuntimeError):
    pass


@dataclass(frozen=True)
class AccountingPoint:
    decoded_rx_frames: int
    runtime_tx_dispatches: int
    decoder_resets_after_tx: int
    ingress_received: int
    ingress_admitted: int
    queue_depth: int
    queue_accepted: int
    queue_dispatched: int
    access_timeouts: int
    downstream_failures: int
    subscriber_drops: int
    failure: str


def _point(engine: TNCEngine) -> AccountingPoint:
    if engine.runtime is None:
        raise P2QualificationError("product runtime accounting is unavailable")
    accounting = engine.runtime.accounting
    runtime = accounting.runtime
    ingress = accounting.ingress
    queue = accounting.queue
    return AccountingPoint(
        decoded_rx_frames=runtime.decoded_rx_frames,
        runtime_tx_dispatches=runtime.tx_dispatches,
        decoder_resets_after_tx=runtime.decoder_resets_after_tx,
        ingress_received=ingress.data_messages_received,
        ingress_admitted=ingress.data_admitted,
        queue_depth=queue.tx_queue_depth,
        queue_accepted=queue.tx_queue_accepted,
        queue_dispatched=queue.tx_dispatched,
        access_timeouts=queue.tx_access_timeouts,
        downstream_failures=queue.tx_downstream_failures,
        subscriber_drops=accounting.subscriber_drops,
        failure=runtime.failure,
    )


def _qualified_tx_config(config: TNCConfig) -> TNCConfig:
    if config.tx_enabled:
        raise P2QualificationError(
            "P2 must start from an operator config with radio.tx_enabled=false"
        )
    if config.frequency_hz != QUALIFIED_TX_FREQUENCY_HZ or config.tx_power != QUALIFIED_TX_POWER:
        raise P2QualificationError("P2 requires the qualified 145.050 MHz / power-200 profile")
    if not config.kiss.enabled or config.kiss.listen != "127.0.0.1" or config.kiss.port != 8001:
        raise P2QualificationError("P2 requires TCP KISS on 127.0.0.1:8001")
    qualified = replace(config, tx_enabled=True)
    validate_config(qualified)
    return qualified


def _wait_dispatch(engine: TNCEngine, before: AccountingPoint, timeout: float) -> AccountingPoint:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        engine.check_health()
        now = _point(engine)
        if now.failure:
            raise P2QualificationError(f"runtime failure: {now.failure}")
        if now.access_timeouts != before.access_timeouts:
            raise P2QualificationError("one-shot TX request timed out in channel access")
        if now.downstream_failures != before.downstream_failures:
            raise P2QualificationError("one-shot TX request failed downstream")
        expected = (
            before.ingress_received + 1,
            before.ingress_admitted + 1,
            before.queue_accepted + 1,
            before.queue_dispatched + 1,
            before.runtime_tx_dispatches + 1,
            before.decoder_resets_after_tx + 1,
        )
        observed = (
            now.ingress_received,
            now.ingress_admitted,
            now.queue_accepted,
            now.queue_dispatched,
            now.runtime_tx_dispatches,
            now.decoder_resets_after_tx,
        )
        if observed == expected and now.queue_depth == 0:
            return now
        if any(value > limit for value, limit in zip(observed, expected)):
            raise P2QualificationError(
                f"more than one TX request/dispatch observed: expected={expected} observed={observed}"
            )
        time.sleep(0.02)
    raise P2QualificationError("timed out waiting for exactly one qualified TX dispatch")


def _hold_no_second_dispatch(engine: TNCEngine, after: AccountingPoint, seconds: float) -> AccountingPoint:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        engine.check_health()
        now = _point(engine)
        if (
            now.ingress_received != after.ingress_received
            or now.ingress_admitted != after.ingress_admitted
            or now.queue_accepted != after.queue_accepted
            or now.queue_dispatched != after.queue_dispatched
            or now.runtime_tx_dispatches != after.runtime_tx_dispatches
            or now.decoder_resets_after_tx != after.decoder_resets_after_tx
            or now.queue_depth != 0
        ):
            raise P2QualificationError("counter changed during no-second-dispatch hold")
        if now.failure or now.access_timeouts != after.access_timeouts or now.downstream_failures != after.downstream_failures:
            raise P2QualificationError("runtime became unhealthy during no-second-dispatch hold")
        time.sleep(0.05)
    return _point(engine)


def _drain_pending_rx(sock: socket.socket) -> int:
    drained = 0
    sock.setblocking(False)
    try:
        while True:
            try:
                chunk = sock.recv(4096)
            except BlockingIOError:
                break
            if not chunk:
                raise P2QualificationError("KISS connection closed before RX recovery gate")
            drained += len(chunk)
    finally:
        sock.setblocking(True)
    return drained


def _wait_live_rx(
    sock: socket.socket,
    engine: TNCEngine,
    *,
    rx_baseline: int,
    tx_dispatches_expected: int,
    timeout: float,
) -> dict[str, object]:
    decoder = KISSStreamDecoder(max_body_bytes=4096)
    deadline = time.monotonic() + timeout
    sock.settimeout(1.0)
    while time.monotonic() < deadline:
        engine.check_health()
        if _point(engine).runtime_tx_dispatches != tx_dispatches_expected:
            raise P2QualificationError("unexpected second TX dispatch while waiting for return RX")
        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            continue
        if not chunk:
            raise P2QualificationError("same KISS connection closed before return RX")
        for message in decoder.feed(chunk):
            if message.port != 0 or message.command != DATA or not message.frame:
                continue
            try:
                parsed = parse_frame(message.frame, has_fcs=False)
            except ValueError:
                continue
            counter_deadline = time.monotonic() + 2.0
            while time.monotonic() < counter_deadline:
                point = _point(engine)
                if point.decoded_rx_frames >= rx_baseline + 1:
                    if point.runtime_tx_dispatches != tx_dispatches_expected:
                        raise P2QualificationError("TX dispatch counter changed during RX recovery")
                    return {
                        "frame": message.frame,
                        "parsed": parsed,
                        "point": point,
                    }
                time.sleep(0.01)
            raise P2QualificationError("KISS RX arrived but runtime RX counter did not advance")
    raise P2QualificationError("timed out waiting for same-connection live RF RX after TX")


def _print_point(prefix: str, point: AccountingPoint) -> None:
    print(f"{prefix}_INGRESS_RECEIVED={point.ingress_received}")
    print(f"{prefix}_INGRESS_ADMITTED={point.ingress_admitted}")
    print(f"{prefix}_QUEUE_ACCEPTED={point.queue_accepted}")
    print(f"{prefix}_QUEUE_DISPATCHED={point.queue_dispatched}")
    print(f"{prefix}_RUNTIME_TX_DISPATCHES={point.runtime_tx_dispatches}")
    print(f"{prefix}_DECODER_RESETS_AFTER_TX={point.decoder_resets_after_tx}")
    print(f"{prefix}_QUEUE_DEPTH={point.queue_depth}")
    print(f"{prefix}_DECODED_RX_FRAMES={point.decoded_rx_frames}")


def main() -> int:
    parser = argparse.ArgumentParser(prog="ywd-tnc-p2-gate")
    parser.add_argument("--config", default="/etc/ywd-mmdvm-tnc/config.toml")
    parser.add_argument("--authorize", required=True)
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    parser.add_argument("--destination", default=DEFAULT_DESTINATION)
    parser.add_argument("--information", default=DEFAULT_INFORMATION)
    parser.add_argument("--dispatch-timeout", type=float, default=30.0)
    parser.add_argument("--no-second-hold", type=float, default=3.0)
    parser.add_argument("--rx-timeout", type=float, default=120.0)
    args = parser.parse_args()

    if args.authorize != AUTHORIZATION:
        print("YWD_TNC_P2=FAIL:authorization token mismatch", file=sys.stderr)
        return 2
    if args.dispatch_timeout <= 0 or args.no_second_hold < 2.0 or args.rx_timeout <= 0:
        print("YWD_TNC_P2=FAIL:invalid qualification timing", file=sys.stderr)
        return 2

    engine: TNCEngine | None = None
    try:
        operator_config = load_config(args.config)
        tx_config = _qualified_tx_config(operator_config)
        frame = build_ui_frame(
            source=Address.parse(args.source),
            destination=Address.parse(args.destination),
            info=args.information.encode("utf-8"),
            include_fcs=False,
        )
        expected_external = f"{args.source}>{args.destination}:{args.information}"

        print("===== YWD-MMDVM-TNC P2 ONE-SHOT TCP KISS TX GATE =====", flush=True)
        print("RF_FREQUENCY_MHZ=145.050", flush=True)
        print("TX_POWER=200", flush=True)
        print("OPERATOR_CONFIG_TX_ENABLED=NO", flush=True)
        print("QUALIFICATION_TX_AUTHORITY=TEMPORARY_IN_MEMORY", flush=True)
        print("KISS_ENDPOINT=127.0.0.1:8001", flush=True)
        print(f"EXPECTED_EXTERNAL_DECODE={expected_external}", flush=True)
        print(f"TX_FRAME_HEX={frame.hex()}", flush=True)
        print("CLIENT_AUTOMATIC_RETRY=NO", flush=True)

        engine = TNCEngine(tx_config)
        engine.start()
        snap = engine.snapshot
        if not snap.running or not snap.tx_enabled or snap.kiss_listener != ("127.0.0.1", 8001):
            raise P2QualificationError(f"qualified engine did not reach expected running state: {snap}")

        with socket.create_connection(("127.0.0.1", 8001), timeout=3.0) as sock:
            before = _point(engine)
            _print_point("BASELINE", before)
            wire = encode(frame, port=0, command=DATA)

            # P2's entire application-side TX action is this one call. There is
            # no retry loop and no second KISS DATA construction/dispatch path.
            sock.sendall(wire)
            print("KISS_DATA_REQUESTS_SENT=1", flush=True)
            print("SOCKET_SENDALL_CALLS=1", flush=True)
            print("CLIENT_RETRY_COUNT=0", flush=True)

            dispatched = _wait_dispatch(engine, before, args.dispatch_timeout)
            _print_point("POST_TX", dispatched)
            print("TX_QUEUE_ACCEPTED_DELTA=1", flush=True)
            print("TX_QUEUE_DISPATCHED_DELTA=1", flush=True)
            print("TX_RUNTIME_DISPATCH_DELTA=1", flush=True)
            print("RX_DECODER_RESET_DELTA=1", flush=True)
            print("TX_QUEUE_DEPTH_AFTER_DISPATCH=0", flush=True)

            held = _hold_no_second_dispatch(engine, dispatched, args.no_second_hold)
            print(f"NO_SECOND_INTERNAL_DISPATCH_HOLD_SECONDS={args.no_second_hold:g}", flush=True)
            print("NO_SECOND_INTERNAL_DISPATCH_AFTER_HOLD=PASS", flush=True)
            print(f"TX_DISPATCHES_AFTER_HOLD={held.runtime_tx_dispatches}", flush=True)

            print("===== INDEPENDENT OVER-AIR DECODE GATE =====", flush=True)
            print(f"EXPECTED_EXTERNAL_DECODE={expected_external}", flush=True)
            print(
                f"After an independent receiver decoded that exact frame ONCE, type {EXTERNAL_CONFIRMATION}:",
                flush=True,
            )
            confirmation = input().strip()
            if confirmation != EXTERNAL_CONFIRMATION:
                raise P2QualificationError("independent external decode was not confirmed")
            print("INDEPENDENT_EXTERNAL_DECODE_CONFIRMED=YES", flush=True)

            held = _hold_no_second_dispatch(engine, held, 2.0)
            drained = _drain_pending_rx(sock)
            rx_baseline = _point(engine).decoded_rx_frames
            print(f"PRE_RETURN_RX_DRAINED_KISS_BYTES={drained}", flush=True)
            print(f"RETURN_RX_BASELINE_DECODED_FRAMES={rx_baseline}", flush=True)
            print("===== SAME-CONNECTION LIVE RX RECOVERY GATE =====", flush=True)
            print("Generate ONE normal 1200-baud AX.25 packet on 145.050 MHz now.", flush=True)

            result = _wait_live_rx(
                sock,
                engine,
                rx_baseline=rx_baseline,
                tx_dispatches_expected=held.runtime_tx_dispatches,
                timeout=args.rx_timeout,
            )
            parsed = result["parsed"]
            rx_frame = result["frame"]
            final = result["point"]
            assert isinstance(parsed, dict)
            assert isinstance(rx_frame, bytes)
            assert isinstance(final, AccountingPoint)

            print("PRODUCT_CONVERSE_LIVE_RX=PASS", flush=True)
            print("SAME_KISS_CONNECTION_RX=PASS", flush=True)
            print(f"RETURN_RX_FRAME_HEX={rx_frame.hex()}", flush=True)
            print(f"RETURN_RX_SOURCE={parsed['source']}", flush=True)
            print(f"RETURN_RX_DESTINATION={parsed['destination']}", flush=True)
            print("RETURN_RX_PATH=" + ",".join(str(item) for item in parsed["path"]), flush=True)
            print(f"RETURN_RX_FRAME_TYPE={parsed['frame_type']}", flush=True)
            pid = parsed["pid"]
            print("RETURN_RX_PID=NONE" if pid is None else f"RETURN_RX_PID=0x{int(pid):02X}", flush=True)
            print("RETURN_RX_INFORMATION=" + bytes(parsed["info"]).decode("utf-8", "backslashreplace"), flush=True)

            if final.runtime_tx_dispatches != before.runtime_tx_dispatches + 1:
                raise P2QualificationError("final TX dispatch count is not exactly baseline + 1")
            if final.queue_accepted != before.queue_accepted + 1 or final.queue_dispatched != before.queue_dispatched + 1:
                raise P2QualificationError("final queue accounting is not exactly one accepted/dispatched")
            if final.queue_depth != 0 or final.access_timeouts != before.access_timeouts or final.downstream_failures != before.downstream_failures:
                raise P2QualificationError("final queue/runtime health is not clean")
            if final.subscriber_drops != 0:
                raise P2QualificationError(f"subscriber drops observed: {final.subscriber_drops}")

            print("TX_DISPATCHES=1", flush=True)
            print("TX_QUEUE_ACCEPTED=1", flush=True)
            print("TX_QUEUE_DISPATCHED=1", flush=True)
            print("SUBSCRIBER_DROPS=0", flush=True)
            print("AUTOMATIC_TX_RETRY=NO", flush=True)
            print("RX_RESUMED_AFTER_TX=PASS", flush=True)
            print("YWD_TNC_P2_PHYSICAL=PASS", flush=True)
            return 0
    except (OSError, ValueError, RuntimeError, EOFError) as exc:
        print(f"YWD_TNC_P2=FAIL:{type(exc).__name__}:{exc}", file=sys.stderr, flush=True)
        return 20
    finally:
        if engine is not None:
            try:
                engine.stop()
            except Exception as exc:  # pragma: no cover - physical cleanup path
                print(f"YWD_TNC_P2_CLEANUP=FAIL:{type(exc).__name__}:{exc}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
