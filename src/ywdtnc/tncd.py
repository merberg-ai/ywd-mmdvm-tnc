"""`ywd-tncd` modem-only service entry point."""

from __future__ import annotations

import argparse
from pathlib import Path
import signal
import sys
import threading

from . import __version__
from .config import TNCConfigurationError, load_config
from .engine import TNCEngine, TNCEngineError


def run_daemon(config_path: str | Path, *, stop_event: threading.Event) -> int:
    config = load_config(config_path)
    engine = TNCEngine(config)
    engine.start()
    try:
        snap = engine.snapshot
        print("YWD_TNCD=RUNNING", flush=True)
        print(f"FIRMWARE_IDENTITY={snap.firmware_identity}", flush=True)
        print(f"TX={'ENABLED' if snap.tx_enabled else 'DISABLED'}", flush=True)
        print(
            "KISS_LISTENER=DISABLED"
            if snap.kiss_listener is None
            else f"KISS_LISTENER={snap.kiss_listener[0]}:{snap.kiss_listener[1]}",
            flush=True,
        )
        print(
            "AGW_RAW_LISTENER=DISABLED"
            if snap.agw_listener is None
            else f"AGW_RAW_LISTENER={snap.agw_listener[0]}:{snap.agw_listener[1]}",
            flush=True,
        )
        print(
            "MONITOR_LISTENER=DISABLED"
            if snap.monitor_listener is None
            else f"MONITOR_LISTENER={snap.monitor_listener[0]}:{snap.monitor_listener[1]}",
            flush=True,
        )
        while not stop_event.wait(0.25):
            engine.check_health()
    finally:
        engine.stop()
        print("YWD_TNCD=STOPPED", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="ywd-tncd")
    parser.add_argument("--config", default="/etc/ywd-mmdvm-tnc/config.toml")
    parser.add_argument(
        "--framework-self-test",
        action="store_true",
        help="validate package/config plumbing without opening UART or transmitting RF",
    )
    args = parser.parse_args()
    config_path = Path(args.config)

    try:
        load_config(config_path)
    except TNCConfigurationError as exc:
        print(f"YWD-MMDVM-TNC {__version__}: configuration error: {exc}", file=sys.stderr)
        return 2

    if args.framework_self_test:
        print("YWD_TNCD_FRAMEWORK_SELF_TEST=PASS")
        print("MODEM_UART_OPENED=NO")
        print("RF_TRANSMITTED=NO")
        return 0

    stop_event = threading.Event()

    def request_stop(signum, frame) -> None:  # type: ignore[no-untyped-def]
        _ = signum, frame
        stop_event.set()

    old_int = signal.getsignal(signal.SIGINT)
    old_term = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    try:
        return run_daemon(config_path, stop_event=stop_event)
    except (TNCConfigurationError, TNCEngineError, RuntimeError, OSError) as exc:
        print(f"YWD-MMDVM-TNC {__version__}: modem service failure: {exc}", file=sys.stderr)
        return 78
    finally:
        signal.signal(signal.SIGINT, old_int)
        signal.signal(signal.SIGTERM, old_term)


if __name__ == "__main__":
    raise SystemExit(main())
