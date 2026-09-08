#!/usr/bin/env python3
"""Layer read-only ADF7021 RSSI telemetry onto qualified AX25R3.

AX25R4 changes no packet waveform, RX slicer, sample timing, FIFO, filter, or
transmit behavior.  It exposes the already-compiled ADF7021 ``CIO::readRSSI()``
path through one additive YWD_RX subcommand (0x05) while passive AX.25 receive
is active and TX is idle.

The returned uint16 is the firmware's raw RSSI magnitude.  This transform does
not choose a carrier threshold or claim DCD semantics; threshold/hysteresis and
live channel qualification belong to the YWD-1278 0C-P2 host layer.
"""
from pathlib import Path
import subprocess
import sys

EXPECTED_TRACKED = [
    "ADF7021.cpp",
    "Config.h",
    "Globals.h",
    "IO.cpp",
    "IO.h",
    "IOSTM.cpp",
    "MMDVM_HS.cpp",
    "SerialPort.cpp",
    "version.h",
]
EXPECTED_UNTRACKED = [
    "AX25AFSKRX.cpp",
    "AX25AFSKRX.h",
    "AX25AFSKTX.cpp",
    "AX25AFSKTX.h",
]


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)


def git_lines(src: Path, *args: str) -> list[str]:
    out = subprocess.check_output(["git", "-C", str(src), *args], text=True)
    return sorted(line for line in out.splitlines() if line)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: apply_ax25_rx4_rssi.py PATH_TO_MMDVM_HS")

    src = Path(sys.argv[1]).resolve()
    if git_lines(src, "diff", "--name-only") != sorted(EXPECTED_TRACKED):
        raise SystemExit("AX25 RX4 requires the exact deterministic AX25R3 transformed tree")
    if git_lines(src, "ls-files", "--others", "--exclude-standard") != sorted(EXPECTED_UNTRACKED):
        raise SystemExit("unexpected generated files before AX25 RX4 transform")
    if (src / "Config.h").read_bytes() != (src / "configs/MMDVM_HS_Hat.h").read_bytes():
        raise SystemExit("Config.h no longer exactly matches pinned simplex-HAT configuration")

    config = (src / "Config.h").read_text()
    adf = (src / "ADF7021.cpp").read_text()
    tx = (src / "AX25AFSKTX.cpp").read_text()
    iostm = (src / "IOSTM.cpp").read_text()
    serial_path = src / "SerialPort.cpp"
    version_path = src / "version.h"
    serial = serial_path.read_text()
    version = version_path.read_text()

    if "YWD-AX25R3-v0.2.2" not in version:
        raise SystemExit("AX25R3 v0.2.2 identity is missing")
    if '#define SEND_RSSI_DATA' not in config:
        raise SystemExit("pinned HAT configuration does not compile ADF7021 RSSI readback")
    if "uint16_t CIO::readRSSI()" not in adf or "AD7021_RB = 0x0147" not in adf:
        raise SystemExit("pinned ADF7021 RSSI ADC readback implementation is missing")
    if "0x000E006FU" not in adf or "5U                         << 20" not in adf:
        raise SystemExit("qualified AX25R3 receive configuration is missing")
    if "TIM2" not in iostm or "19200U" not in iostm:
        raise SystemExit("qualified AX25R3 local 19.2ksps sampler is missing")
    if "CIO_FIFO_RESERVE = 256U" not in tx:
        raise SystemExit("qualified AX25 transmit FIFO reserve is missing")
    if "reply[4U] = 3U; // RX3:" not in serial:
        raise SystemExit("qualified YWD_RX revision-3 status layout is missing")

    serial = replace_once(
        serial,
        "const uint8_t YWD_RX_STATUS      = 0x04U;\n"
        "const uint8_t YWD_RX_MAX_READ    = 200U;\n",
        "const uint8_t YWD_RX_STATUS      = 0x04U;\n"
        "const uint8_t YWD_RX_RSSI        = 0x05U;\n"
        "const uint8_t YWD_RX_MAX_READ    = 200U;\n",
        "YWD_RX RSSI subcommand constant",
    )

    old_tail = r'''              writeInt(1U, reply, 14U);
            } else {
              sendNAK(4U);
            }
            break;
          }
'''
    new_tail = r'''              writeInt(1U, reply, 14U);
            } else if (sub == YWD_RX_RSSI) {
              if (m_len != 4U) {
                sendNAK(4U);
                break;
              }
              // RSSI polling is a passive receive-only operation.  Refuse the
              // read unless the qualified AX25 RX capture is active and every
              // TX state is idle, so this telemetry path cannot race RF TX.
              if (!ax25AFSKRX.active() || m_tx || ax25AFSKTX.busy() ||
                  m_modemState != STATE_AX25) {
                sendNAK(5U);
                break;
              }

#if defined(SEND_RSSI_DATA)
              const uint16_t rssi = io.readRSSI();
              uint8_t reply[6U];
              reply[0U] = MMDVM_FRAME_START;
              reply[1U] = 6U;
              reply[2U] = MMDVM_YWD_RX;
              reply[3U] = YWD_RX_RSSI;
              reply[4U] = uint8_t(rssi & 0xFFU);
              reply[5U] = uint8_t((rssi >> 8) & 0xFFU);
              writeInt(1U, reply, 6U);
#else
              sendNAK(6U);
#endif
            } else {
              sendNAK(4U);
            }
            break;
          }
'''
    serial = replace_once(serial, old_tail, new_tail, "YWD_RX RSSI read-only branch")

    serial = replace_once(
        serial,
        '              const char info[] = "YWD-MMDVM-AX25R3";',
        '              const char info[] = "YWD-MMDVM-AX25R4";',
        "AX25R4 info string",
    )
    version = replace_once(
        version,
        "YWD-AX25R3-v0.2.2",
        "YWD-AX25R4-v0.2.3",
        "AX25R4 firmware identity",
    )

    serial_path.write_text(serial)
    version_path.write_text(version)

    if git_lines(src, "diff", "--name-only") != sorted(EXPECTED_TRACKED):
        raise SystemExit("unexpected tracked firmware diff after AX25 RX4 transform")
    if git_lines(src, "ls-files", "--others", "--exclude-standard") != sorted(EXPECTED_UNTRACKED):
        raise SystemExit("unexpected generated files after AX25 RX4 transform")
    if (src / "Config.h").read_bytes() != (src / "configs/MMDVM_HS_Hat.h").read_bytes():
        raise SystemExit("Config.h changed during AX25 RX4 transform")

    subprocess.check_call(["git", "-C", str(src), "diff", "--check"])
    print("AX25_RX4_RSSI_SOURCE_TRANSFORM=PASS")
    print("YWD_RX_RSSI_SUBCOMMAND=0x05")
    print("RSSI_SOURCE=ADF7021_REGISTER7_ADC_READBACK")
    print("RSSI_WIRE_VALUE=UINT16_RAW_MAGNITUDE")
    print("RSSI_REQUIRES_ACTIVE_AX25_RX=YES")
    print("RSSI_BLOCKED_DURING_TX=YES")
    print("RX_STATUS_REVISION=3_UNCHANGED")
    print("AX25R3_RX_FILTER_TIMING_FIFO=UNCHANGED")
    print("QUALIFIED_AX25_TX_PATH=UNCHANGED")
    print("CARRIER_THRESHOLD_SELECTED=NO")
    print("identity: YWD-AX25R4-v0.2.3")


if __name__ == "__main__":
    main()
