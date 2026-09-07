#!/usr/bin/env python3
"""YWD-MMDVM-TNC wrapper for the pinned qualified HAT GPIO helper."""
from __future__ import annotations

import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor" / "ywd-1278" / "firmware" / "hat_control.py"
if not VENDOR.is_file():
    print("YWD-MMDVM-TNC: qualified HAT control backend is missing", file=sys.stderr)
    raise SystemExit(2)
os.execv(sys.executable, [sys.executable, str(VENDOR), *sys.argv[1:]])
