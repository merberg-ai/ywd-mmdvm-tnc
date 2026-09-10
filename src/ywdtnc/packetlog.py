"""Read-only monitor-stream client with human and JSONL packet logs."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import socket
import sys
import time

from .config import TNCConfigurationError, load_config


def _local_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone()
    except (TypeError, ValueError):
        return datetime.now().astimezone()


def _route(event: dict) -> str:
    source = str(event.get("source", "?"))
    destination = str(event.get("destination", "?"))
    path = event.get("path")
    route = f"fm {source} to {destination}"
    if isinstance(path, list) and path:
        route += " via " + " ".join(str(item) for item in path)
    return route


def _control_text(event: dict) -> str:
    frame_class = str(event.get("frame_class", ""))
    frame_type = str(event.get("frame_type", "?"))
    pf = "+" if event.get("poll_final") is True else "-"
    nr = event.get("nr")
    ns = event.get("ns")
    if frame_class == "I" and isinstance(ns, int) and isinstance(nr, int):
        return f"I{ns}{nr}{pf}"
    if frame_class == "S" and isinstance(nr, int):
        return f"{frame_type}{nr}{pf}"
    if frame_class == "U":
        return f"{frame_type}{pf}"
    return frame_type


def _frame_tail(event: dict) -> str:
    parts = [f"ctl {_control_text(event)}"]
    pid = event.get("pid")
    if isinstance(pid, int):
        parts.append(f"pid={pid:02X}")
    info_hex = event.get("info_hex")
    if isinstance(info_hex, str):
        parts.append(f"len={len(info_hex) // 2}")
    info = str(event.get("info_text", ""))
    if info:
        parts.append(" " + info)
    return " ".join(parts)


def format_event(event: dict) -> str:
    stamp = _local_timestamp(str(event.get("ts", ""))).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    kind = str(event.get("event", "unknown"))
    if kind == "rx.frame":
        return f"{stamp} RX {_route(event)} {_frame_tail(event)}"
    if kind.startswith("tx."):
        stage = kind[3:].upper().replace("_", "-")
        request_id = event.get("request_id")
        request = "" if request_id is None else f" #{request_id}"
        frame = ""
        if "source" in event:
            frame = f" {_route(event)} {_frame_tail(event)}"
        extra = ""
        if kind == "tx.channel_clear" and event.get("raw_rssi") is not None:
            extra = f" raw_rssi={event['raw_rssi']}"
        elif kind == "tx.dispatched" and event.get("selector_count") is not None:
            extra = f" selectors={event['selector_count']}"
        elif kind in {"tx.rejected", "tx.timeout"} and event.get("reason"):
            extra = f" reason={event['reason']}"
        elif kind == "tx.failed" and event.get("error"):
            extra = f" error={event['error']}"
        return f"{stamp} TX {stage}{request}{frame}{extra}"
    return f"{stamp} {kind} {json.dumps(event, separators=(',', ':'), sort_keys=True)}"


class PacketLogWriter:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)

    def write(self, event: dict) -> str:
        local = _local_timestamp(str(event.get("ts", "")))
        day = local.strftime("%Y-%m-%d")
        human = format_event(event)
        with (self.directory / f"{day}.log").open("a", encoding="utf-8") as handle:
            handle.write(human + "\n")
        with (self.directory / f"{day}.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, separators=(",", ":"), sort_keys=True) + "\n")
        return human


def run(host: str, port: int, log_dir: Path, *, reconnect_seconds: float = 3.0) -> int:
    writer = PacketLogWriter(log_dir)
    while True:
        try:
            print(f"YWD_PACKETLOG_CONNECTING={host}:{port}", flush=True)
            with socket.create_connection((host, port), timeout=5.0) as sock:
                sock.settimeout(None)
                print(f"YWD_PACKETLOG_CONNECTED={host}:{port}", flush=True)
                with sock.makefile("r", encoding="utf-8", errors="replace", newline="\n") as stream:
                    for line in stream:
                        if not line.strip():
                            continue
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError as exc:
                            print(f"YWD_PACKETLOG_BAD_JSON={exc}", file=sys.stderr, flush=True)
                            continue
                        if not isinstance(event, dict) or event.get("schema") != 1:
                            continue
                        print(writer.write(event), flush=True)
            raise ConnectionError("monitor stream closed")
        except KeyboardInterrupt:
            return 0
        except (OSError, ConnectionError) as exc:
            print(f"YWD_PACKETLOG_RECONNECT={exc}", file=sys.stderr, flush=True)
            time.sleep(reconnect_seconds)


def main() -> int:
    parser = argparse.ArgumentParser(prog="ywd-packetlog")
    parser.add_argument("--config", default="/etc/ywd-mmdvm-tnc/config.toml")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--log-dir", default="/var/log/ywd-packetlog")
    parser.add_argument("--reconnect-seconds", type=float, default=3.0)
    args = parser.parse_args()
    try:
        config = load_config(args.config)
    except TNCConfigurationError as exc:
        print(f"ywd-packetlog: configuration error: {exc}", file=sys.stderr)
        return 2
    if not config.monitor.enabled and (args.host is None or args.port is None):
        print("ywd-packetlog: [monitor] is disabled; enable it or supply --host and --port", file=sys.stderr)
        return 2
    host = args.host or config.monitor.listen
    port = args.port or config.monitor.port
    if host == "0.0.0.0":
        host = "127.0.0.1"
    if not 1 <= port <= 65535 or args.reconnect_seconds <= 0:
        print("ywd-packetlog: invalid port or reconnect interval", file=sys.stderr)
        return 2
    return run(host, port, Path(args.log_dir), reconnect_seconds=args.reconnect_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
