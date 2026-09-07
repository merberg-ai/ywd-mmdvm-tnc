"""RX-only physical qualification helper for YWD-MMDVM-TNC TCP KISS.

The helper connects to an already-running KISS listener and waits for one live
port-0 DATA frame. It never writes to the socket, so it cannot request RF TX.
"""

from __future__ import annotations

import argparse
import socket
import sys
import time

from ywd1278.ax25.codec import parse_frame
from ywd1278.kiss.framing import DATA, KISSStreamDecoder


def _text(data: bytes) -> str:
    return data.decode("utf-8", "backslashreplace")


def main() -> int:
    parser = argparse.ArgumentParser(prog="ywd-tnc-rx-gate")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()

    if not 1 <= args.port <= 65535:
        parser.error("--port must be 1..65535")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")

    deadline = time.monotonic() + args.timeout
    decoder = KISSStreamDecoder(max_body_bytes=4096)

    print("===== YWD-MMDVM-TNC P1 LIVE RX GATE =====", flush=True)
    print(f"KISS_ENDPOINT={args.host}:{args.port}", flush=True)
    print(f"WAIT_LIMIT_SECONDS={args.timeout:g}", flush=True)
    print("KISS_BYTES_SENT=0", flush=True)
    print("TX_REQUESTED=NO", flush=True)

    try:
        with socket.create_connection((args.host, args.port), timeout=min(args.timeout, 5.0)) as sock:
            sock.settimeout(1.0)
            print("KISS_CONNECTED=YES", flush=True)
            while time.monotonic() < deadline:
                try:
                    chunk = sock.recv(4096)
                except socket.timeout:
                    continue
                if not chunk:
                    print("YWD_TNC_P1_LIVE_RX=FAIL:KISS_CONNECTION_CLOSED", file=sys.stderr)
                    return 21
                for message in decoder.feed(chunk):
                    if message.port != 0 or message.command != DATA or not message.frame:
                        continue
                    try:
                        parsed = parse_frame(message.frame, has_fcs=False)
                    except ValueError as exc:
                        print(f"KISS_DATA_PARSE_REJECTED={exc}", flush=True)
                        continue

                    print("YWD_TNC_P1_LIVE_RX=PASS", flush=True)
                    print(f"FRAME_BYTES={len(message.frame)}", flush=True)
                    print(f"FRAME_HEX={message.frame.hex()}", flush=True)
                    print(f"AX25_SOURCE={parsed['source']}", flush=True)
                    print(f"AX25_DESTINATION={parsed['destination']}", flush=True)
                    print(
                        "AX25_PATH=" + ",".join(str(item) for item in parsed["path"]),
                        flush=True,
                    )
                    print(f"AX25_FRAME_TYPE={parsed['frame_type']}", flush=True)
                    pid = parsed["pid"]
                    print(
                        "AX25_PID=NONE" if pid is None else f"AX25_PID=0x{int(pid):02X}",
                        flush=True,
                    )
                    print(f"AX25_INFORMATION={_text(parsed['info'])}", flush=True)
                    print("KISS_BYTES_SENT=0", flush=True)
                    print("TX_REQUESTED=NO", flush=True)
                    print("PHYSICAL_GATE_RF_DIRECTION=RX_ONLY", flush=True)
                    return 0
    except OSError as exc:
        print(f"YWD_TNC_P1_LIVE_RX=FAIL:KISS_CONNECT:{exc}", file=sys.stderr)
        return 20

    print("YWD_TNC_P1_LIVE_RX=FAIL:TIMEOUT", file=sys.stderr)
    print("KISS_BYTES_SENT=0")
    print("TX_REQUESTED=NO")
    return 22


if __name__ == "__main__":
    raise SystemExit(main())
