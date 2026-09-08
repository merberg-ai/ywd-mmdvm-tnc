#!/usr/bin/env python3
"""Finalize Classic-1 v0.1.4 with a non-full CIO refill strategy.

Applied after the v0.1.3 continuity transform.  The AX25AFSKTX source copied by
the base transform already contains the 256-bit CIO reserve / 768-bit refill
strategy.  This layer verifies that invariant and bumps the development
identity so hardware tests can distinguish the sample-loss fix.
"""
from pathlib import Path
import subprocess
import sys


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: apply_ax25_classic1_reserve.py PATH_TO_MMDVM_HS")

    src = Path(sys.argv[1]).resolve()
    tx_path = src / "AX25AFSKTX.cpp"
    version_path = src / "version.h"

    tx = tx_path.read_text()
    version = version_path.read_text()

    if "YWD-AX25C1-v0.1.3" not in version:
        raise SystemExit("Classic-1 v0.1.3 identity is missing before FIFO reserve transform")
    if "CIO_FIFO_RESERVE = 256U" not in tx:
        raise SystemExit("256-bit CIO reserve is missing")
    if "CIO_REFILL_MAX = 768U" not in tx:
        raise SystemExit("768-bit CIO refill target is missing")
    if "space - CIO_FIFO_RESERVE" not in tx:
        raise SystemExit("conservative CIO refill budget is missing")

    version = replace_once(
        version,
        "YWD-AX25C1-v0.1.3",
        "YWD-AX25C1-v0.1.4",
        "FIFO reserve firmware identity",
    )
    version_path.write_text(version)

    subprocess.check_call(["git", "-C", str(src), "diff", "--check"])
    print("AX25_CLASSIC1_RESERVE_TRANSFORM=PASS")
    print("CIO policy: keep 256 bits free; refill toward 768-bit occupancy")
    print("goal: never enter stock CBitRB full/head==tail state during AX25 TX")
    print("Bell-202 NCO / ADF7021 clock / deviation unchanged")
    print("identity bumped to YWD-AX25C1-v0.1.4")


if __name__ == "__main__":
    main()
