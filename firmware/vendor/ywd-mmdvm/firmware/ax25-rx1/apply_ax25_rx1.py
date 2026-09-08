#!/usr/bin/env python3
"""Layer AX25-2A raw receive feasibility onto qualified AX25C1 v0.1.4.

The RX probe is intentionally passive.  It captures the ADF7021 clocked RXD
bit in STATE_AX25, packs samples into a bounded FIFO, and exposes them through
a separate 0x59 host namespace for Pi-side analysis.  Existing modem RX paths
are untouched outside STATE_AX25 and no RX command can key RF.
"""
from pathlib import Path
import shutil
import subprocess
import sys

EXPECTED_BEFORE_TRACKED = [
    "ADF7021.cpp",
    "Config.h",
    "Globals.h",
    "IO.cpp",
    "MMDVM_HS.cpp",
    "SerialPort.cpp",
    "version.h",
]
EXPECTED_BEFORE_UNTRACKED = ["AX25AFSKTX.cpp", "AX25AFSKTX.h"]
EXPECTED_AFTER_UNTRACKED = [
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
        raise SystemExit("usage: apply_ax25_rx1.py PATH_TO_MMDVM_HS")

    src = Path(sys.argv[1]).resolve()
    here = Path(__file__).resolve().parent

    if git_lines(src, "diff", "--name-only") != sorted(EXPECTED_BEFORE_TRACKED):
        raise SystemExit("AX25 RX1 requires the exact deterministic AX25C1 v0.1.4 transformed tree")
    if git_lines(src, "ls-files", "--others", "--exclude-standard") != sorted(EXPECTED_BEFORE_UNTRACKED):
        raise SystemExit("unexpected generated files before AX25 RX1 transform")
    if (src / "Config.h").read_bytes() != (src / "configs/MMDVM_HS_Hat.h").read_bytes():
        raise SystemExit("Config.h no longer exactly matches pinned simplex-HAT configuration")

    globals_path = src / "Globals.h"
    main_path = src / "MMDVM_HS.cpp"
    adf_path = src / "ADF7021.cpp"
    serial_path = src / "SerialPort.cpp"
    version_path = src / "version.h"

    globals_h = globals_path.read_text()
    main_cpp = main_path.read_text()
    adf = adf_path.read_text()
    serial = serial_path.read_text()
    version = version_path.read_text()

    if "YWD-AX25C1-v0.1.4" not in version:
        raise SystemExit("qualified AX25C1 v0.1.4 identity is missing")
    if "CIO_FIFO_RESERVE = 256U" not in (src / "AX25AFSKTX.cpp").read_text():
        raise SystemExit("qualified AX25C1 FIFO-reserve fix is missing")
    if "YWD_RF_GET_DIAG" not in serial:
        raise SystemExit("qualified AX25C1 diagnostics are missing")

    shutil.copy2(here / "AX25AFSKRX.cpp", src / "AX25AFSKRX.cpp")
    shutil.copy2(here / "AX25AFSKRX.h", src / "AX25AFSKRX.h")

    globals_h = replace_once(
        globals_h,
        '#include "AX25AFSKTX.h"\n',
        '#include "AX25AFSKTX.h"\n#include "AX25AFSKRX.h"\n',
        "AX25 RX include",
    )
    globals_h = replace_once(
        globals_h,
        "extern CAX25AFSKTX ax25AFSKTX;\n",
        "extern CAX25AFSKTX ax25AFSKTX;\nextern CAX25AFSKRX ax25AFSKRX;\n",
        "AX25 RX extern",
    )

    main_cpp = replace_once(
        main_cpp,
        "CAX25AFSKTX ax25AFSKTX;\n\nCSerialPort serial;",
        "CAX25AFSKTX ax25AFSKTX;\nCAX25AFSKRX ax25AFSKRX;\n\nCSerialPort serial;",
        "AX25 RX global",
    )

    adf = replace_once(
        adf,
        "    m_rxBuffer.put(bit, m_control);\n  }\n\n  if (torx_request",
        "    if (m_modemState == STATE_AX25)\n"
        "      ax25AFSKRX.sample(bit);\n"
        "    else\n"
        "      m_rxBuffer.put(bit, m_control);\n"
        "  }\n\n  if (torx_request",
        "AX25 raw RX ISR tap",
    )

    serial = replace_once(
        serial,
        "const uint8_t MMDVM_YWD_RF       = 0x58U;\n",
        "const uint8_t MMDVM_YWD_RF       = 0x58U;\n"
        "const uint8_t MMDVM_YWD_RX       = 0x59U;\n",
        "YWD RX command namespace",
    )
    serial = replace_once(
        serial,
        "const uint8_t YWD_RF_GET_DIAG    = 0x05U;\n",
        "const uint8_t YWD_RF_GET_DIAG    = 0x05U;\n\n"
        "const uint8_t YWD_RX_START       = 0x01U;\n"
        "const uint8_t YWD_RX_READ        = 0x02U;\n"
        "const uint8_t YWD_RX_STOP        = 0x03U;\n"
        "const uint8_t YWD_RX_STATUS      = 0x04U;\n"
        "const uint8_t YWD_RX_MAX_READ    = 200U;\n",
        "YWD RX subcommands",
    )

    serial = replace_once(
        serial,
        "            } else if (sub == YWD_RF_TX_TONES) {\n              if (m_len < 7U || !m_ywdRFReady) {",
        "            } else if (sub == YWD_RF_TX_TONES) {\n"
        "              if (ax25AFSKRX.active()) {\n"
        "                sendNAK(5U);\n"
        "                break;\n"
        "              }\n"
        "              if (m_len < 7U || !m_ywdRFReady) {",
        "block TX while RX capture is active",
    )
    serial = replace_once(
        serial,
        "              if (ax25AFSKTX.busy() || m_tx) {",
        "              if (ax25AFSKTX.busy() || m_tx || ax25AFSKRX.active()) {",
        "block RF exit while RX capture is active",
    )

    rx_case = r'''          case MMDVM_YWD_RX: {
            // AX25-2A passive raw receive-slicer probe. No subcommand keys RF.
            if (m_len < 4U) {
              sendNAK(4U);
              break;
            }

            const uint8_t sub = m_buffer[3U];
            if (sub == YWD_RX_START) {
              if (m_len != 4U) {
                sendNAK(4U);
                break;
              }
              if (!m_ywdRFReady || m_tx || ax25AFSKTX.busy() || ax25AFSKRX.active() ||
                  (m_modemState != STATE_IDLE && m_modemState != STATE_AX25)) {
                sendNAK(5U);
                break;
              }

              ax25AFSKRX.reset();
              // Configure the qualified 19.2 kHz AX25 ADF7021 state explicitly.
              // Calling ifConf first sets m_modemState_prev, so setMode updates
              // logical state/LEDs without programming the RF chip twice.
              io.ifConf(STATE_AX25, true);
              setMode(STATE_AX25);
              ax25AFSKRX.start();
              sendACK();
            } else if (sub == YWD_RX_READ) {
              if (m_len != 5U || m_buffer[4U] == 0U || m_buffer[4U] > YWD_RX_MAX_READ) {
                sendNAK(4U);
                break;
              }

              uint8_t reply[205U];
              const uint8_t count = ax25AFSKRX.read(reply + 5U, m_buffer[4U]);
              reply[0U] = MMDVM_FRAME_START;
              reply[1U] = uint8_t(5U + count);
              reply[2U] = MMDVM_YWD_RX;
              reply[3U] = YWD_RX_READ;
              reply[4U] = count;
              writeInt(1U, reply, uint8_t(5U + count));
            } else if (sub == YWD_RX_STOP) {
              if (m_len != 4U) {
                sendNAK(4U);
                break;
              }

              ax25AFSKRX.stop();
              if (m_modemState == STATE_AX25 && !m_tx && !ax25AFSKTX.busy())
                setMode(STATE_IDLE);
              sendACK();
            } else if (sub == YWD_RX_STATUS) {
              if (m_len != 4U) {
                sendNAK(4U);
                break;
              }

              uint8_t flags = 0U;
              if (ax25AFSKRX.active())
                flags |= 0x01U;
              if (m_tx)
                flags |= 0x02U;
              if (m_ywdRFReady)
                flags |= 0x04U;
              if (m_modemState == STATE_AX25)
                flags |= 0x08U;

              const uint16_t available = ax25AFSKRX.available();
              const uint32_t samples = ax25AFSKRX.samples();
              const uint16_t dropped = ax25AFSKRX.droppedBytes();
              uint8_t reply[14U];
              reply[0U] = MMDVM_FRAME_START;
              reply[1U] = 14U;
              reply[2U] = MMDVM_YWD_RX;
              reply[3U] = YWD_RX_STATUS;
              reply[4U] = 1U; // raw RX capture protocol revision
              reply[5U] = flags;
              reply[6U] = uint8_t(available & 0xFFU);
              reply[7U] = uint8_t((available >> 8) & 0xFFU);
              reply[8U] = uint8_t(samples & 0xFFU);
              reply[9U] = uint8_t((samples >> 8) & 0xFFU);
              reply[10U] = uint8_t((samples >> 16) & 0xFFU);
              reply[11U] = uint8_t((samples >> 24) & 0xFFU);
              reply[12U] = uint8_t(dropped & 0xFFU);
              reply[13U] = uint8_t((dropped >> 8) & 0xFFU);
              writeInt(1U, reply, 14U);
            } else {
              sendNAK(4U);
            }
            break;
          }

'''
    serial = replace_once(
        serial,
        "          case MMDVM_GET_STATUS:\n",
        rx_case + "          case MMDVM_GET_STATUS:\n",
        "YWD RX switch case",
    )

    serial = replace_once(
        serial,
        '              const char info[] = "YWD-MMDVM-AX25C1";',
        '              const char info[] = "YWD-MMDVM-AX25R1";',
        "AX25 RX info string",
    )
    version = replace_once(
        version,
        "YWD-AX25C1-v0.1.4",
        "YWD-AX25R1-v0.2.0",
        "AX25 RX probe firmware identity",
    )

    globals_path.write_text(globals_h)
    main_path.write_text(main_cpp)
    adf_path.write_text(adf)
    serial_path.write_text(serial)
    version_path.write_text(version)

    if git_lines(src, "ls-files", "--others", "--exclude-standard") != sorted(EXPECTED_AFTER_UNTRACKED):
        raise SystemExit("unexpected generated files after AX25 RX1 transform")
    if (src / "Config.h").read_bytes() != (src / "configs/MMDVM_HS_Hat.h").read_bytes():
        raise SystemExit("Config.h changed during AX25 RX1 transform")

    subprocess.check_call(["git", "-C", str(src), "diff", "--check"])
    print("AX25_RX1_SOURCE_TRANSFORM=PASS")
    print("RX path: ADF7021 RXD sampled at clock edge -> packed 512-byte FIFO -> host 0x59")
    print("sample rate target: qualified AX25 state at 19.2 ksample/s")
    print("existing non-AX25 receive paths unchanged")
    print("RX namespace contains no RF transmit operation")
    print("identity: YWD-AX25R1-v0.2.0")


if __name__ == "__main__":
    main()
