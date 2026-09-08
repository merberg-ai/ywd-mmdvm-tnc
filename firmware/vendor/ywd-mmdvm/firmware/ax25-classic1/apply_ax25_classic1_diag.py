#!/usr/bin/env python3
"""Add Classic-1 RF diagnostics after the base AX25C1 transform.

This adds one read-only YWD_RF diagnostic subcommand.  The accompanying
AX25AFSKTX source uses a bounded CIO feeder and intentionally keeps headroom in
the stock 1024-bit TX ring so Classic-1 does not exercise the ring's ambiguous
full/head==tail state.  This layer still bumps the base identity to v0.1.2;
later deterministic transforms layer continuity and current development fixes.
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
        raise SystemExit("usage: apply_ax25_classic1_diag.py PATH_TO_MMDVM_HS")

    src = Path(sys.argv[1]).resolve()
    serial_path = src / "SerialPort.cpp"
    version_path = src / "version.h"
    tx_path = src / "AX25AFSKTX.cpp"

    serial = serial_path.read_text()
    version = version_path.read_text()
    tx = tx_path.read_text()

    if "YWD_RF_GET_STATUS  = 0x01U" not in serial or "YWD_RF_EXIT        = 0x04U" not in serial:
        raise SystemExit("Classic-1 RF namespace is missing")
    if "YWD-AX25C1-v0.1.0" not in version:
        raise SystemExit("Classic-1 v0.1.0 identity is missing before diagnostics transform")
    if "CIO_FIFO_RESERVE = 256U" not in tx or "CIO_REFILL_MAX = 768U" not in tx:
        raise SystemExit("Classic-1 bounded CIO reserve/refill feeder is missing")
    if "io.write(samples, count);" not in tx:
        raise SystemExit("Classic-1 bounded FIFO write is missing from AX25AFSKTX.cpp")

    serial = replace_once(
        serial,
        "const uint8_t YWD_RF_EXIT        = 0x04U;\n",
        "const uint8_t YWD_RF_EXIT        = 0x04U;\n"
        "const uint8_t YWD_RF_GET_DIAG    = 0x05U;\n",
        "diagnostic subcommand constant",
    )

    diag_case = r'''            } else if (sub == YWD_RF_GET_DIAG) {
              if (m_len != 4U) {
                sendNAK(4U);
                break;
              }

              uint16_t int1 = 0U;
              uint16_t int2 = 0U;
              io.getIntCounter(int1, int2);
              const uint16_t samples = ax25AFSKTX.samplesQueued();
              uint8_t reply[10U];
              reply[0U] = MMDVM_FRAME_START;
              reply[1U] = 10U;
              reply[2U] = MMDVM_YWD_RF;
              reply[3U] = YWD_RF_GET_DIAG;
              reply[4U] = uint8_t(int1 & 0xFFU);
              reply[5U] = uint8_t((int1 >> 8) & 0xFFU);
              reply[6U] = ax25AFSKTX.keyups();
              reply[7U] = uint8_t(samples & 0xFFU);
              reply[8U] = uint8_t((samples >> 8) & 0xFFU);
              reply[9U] = m_tx ? 1U : 0U;
              writeInt(1U, reply, 10U);
'''

    serial = replace_once(
        serial,
        "            } else {\n              sendNAK(4U);\n            }\n            break;\n          }\n\n          case MMDVM_GET_STATUS:",
        diag_case
        + "            } else {\n              sendNAK(4U);\n            }\n            break;\n          }\n\n          case MMDVM_GET_STATUS:",
        "diagnostic RF switch case",
    )

    version = replace_once(
        version,
        "YWD-AX25C1-v0.1.0",
        "YWD-AX25C1-v0.1.2",
        "diagnostic firmware identity",
    )

    serial_path.write_text(serial)
    version_path.write_text(version)

    subprocess.check_call(["git", "-C", str(src), "diff", "--check"])
    print("AX25_CLASSIC1_DIAG_TRANSFORM=PASS")
    print("diagnostics: YWD_RF 0x05 GET_DIAG = INT1/sample counter + keyups + generated samples + m_tx")
    print("TX feeder: bounded CIO writes with 256-bit reserve / 768-bit refill target")
    print("identity bumped to YWD-AX25C1-v0.1.2")


if __name__ == "__main__":
    main()
