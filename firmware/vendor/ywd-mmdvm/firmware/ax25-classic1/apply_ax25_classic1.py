#!/usr/bin/env python3
"""Layer classic 1200-baud AX.25 TX feasibility onto qualified Stage 4.

The build wrapper first recreates exact Stage 4 from pinned upstream 7ff74ed.
This transformer then adds only a dedicated experimental Bell-202 transmit
state and explicit RF command. Existing DMR and other modem behavior is left
untouched.
"""
from pathlib import Path
import shutil
import subprocess
import sys

EXPECTED_STAGE4_TRACKED = ["Config.h", "SerialPort.cpp", "version.h"]
EXPECTED_FINAL_TRACKED = [
    "ADF7021.cpp",
    "Config.h",
    "Globals.h",
    "MMDVM_HS.cpp",
    "SerialPort.cpp",
    "version.h",
]
EXPECTED_UNTRACKED = ["AX25AFSKTX.cpp", "AX25AFSKTX.h"]
DUPLEX_BOUNDARY = "\n#if defined(DUPLEX)\nvoid CIO::ifConf2(MMDVM_STATE modemState)\n"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one anchor, found {count}")
    return text.replace(old, new, 1)


def git_lines(src: Path, *args: str) -> list[str]:
    out = subprocess.check_output(["git", "-C", str(src), *args], text=True)
    return [line for line in out.splitlines() if line]


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: apply_ax25_classic1.py PATH_TO_MMDVM_HS")

    src = Path(sys.argv[1]).resolve()
    here = Path(__file__).resolve().parent

    if git_lines(src, "diff", "--name-only") != EXPECTED_STAGE4_TRACKED:
        raise SystemExit("AX25 classic-1 requires the exact deterministic Stage 4 transformed tree")
    if git_lines(src, "ls-files", "--others", "--exclude-standard"):
        raise SystemExit("unexpected untracked files exist before AX25 classic-1 transform")
    if (src / "Config.h").read_bytes() != (src / "configs/MMDVM_HS_Hat.h").read_bytes():
        raise SystemExit("Config.h no longer exactly matches pinned simplex-HAT configuration")

    serial_path = src / "SerialPort.cpp"
    globals_path = src / "Globals.h"
    main_path = src / "MMDVM_HS.cpp"
    adf_path = src / "ADF7021.cpp"
    version_path = src / "version.h"

    serial = serial_path.read_text()
    globals_h = globals_path.read_text()
    main_cpp = main_path.read_text()
    adf = adf_path.read_text()
    version = version_path.read_text()

    if "MMDVM_YWD_CONTROL  = 0x56U" not in serial or "MMDVM_YWD_DATA     = 0x57U" not in serial:
        raise SystemExit("qualified Stage 4 host commands are missing")
    if "YWD-STAGE4-v0.0.3" not in version:
        raise SystemExit("qualified Stage 4 identity is missing")

    globals_h = replace_once(
        globals_h,
        "  STATE_M17       = 7,\n\n  // Dummy states start at 90",
        "  STATE_M17       = 7,\n  STATE_AX25      = 8,   // YWD experimental classic 1200-baud packet TX\n\n  // Dummy states start at 90",
        "classic AX25 modem state",
    )
    globals_h = replace_once(
        globals_h,
        '#include "CWIdTX.h"\n#include "CalRSSI.h"',
        '#include "CWIdTX.h"\n#include "AX25AFSKTX.h"\n#include "CalRSSI.h"',
        "AX25AFSKTX include",
    )
    globals_h = replace_once(
        globals_h,
        "extern CCWIdTX cwIdTX;\n",
        "extern CCWIdTX cwIdTX;\nextern CAX25AFSKTX ax25AFSKTX;\n",
        "AX25AFSKTX extern",
    )

    main_cpp = replace_once(
        main_cpp,
        "CCWIdTX    cwIdTX;\n\nCSerialPort serial;",
        "CCWIdTX    cwIdTX;\nCAX25AFSKTX ax25AFSKTX;\n\nCSerialPort serial;",
        "AX25AFSKTX global",
    )
    main_cpp = replace_once(
        main_cpp,
        "  if (m_m17Enable && m_modemState == STATE_M17)\n    m17TX.process();\n\n  if (m_pocsagEnable",
        "  if (m_m17Enable && m_modemState == STATE_M17)\n    m17TX.process();\n\n  if (m_modemState == STATE_AX25)\n    ax25AFSKTX.process();\n\n  if (m_pocsagEnable",
        "AX25AFSKTX scheduler",
    )

    if adf.count(DUPLEX_BOUNDARY) != 1:
        raise SystemExit("could not uniquely separate primary CIO::ifConf from duplex CIO::ifConf2")
    primary, duplex_tail = adf.split(DUPLEX_BOUNDARY, 1)
    original_duplex_tail = duplex_tail

    primary = replace_once(
        primary,
        "    case STATE_DMR:\n    case STATE_CWID:\n      AFC_OFFSET = AFC_OFFSET_DMR;",
        "    case STATE_AX25:\n    case STATE_DMR:\n    case STATE_CWID:\n      AFC_OFFSET = AFC_OFFSET_DMR;",
        "classic AX25 AFC",
    )

    ax25_adf_case = '''    case STATE_AX25:\n      // Classic AX.25 feasibility mode. The host supplies one Bell-202 tone\n      // selector per 1200-baud bit. The STM32 expands that to a 19.2 kbps\n      // one-bit waveform, and the ADF7021 2FSK deviation acts as a 1-bit FM\n      // baseband DAC. A normal FM receiver should recover 1200/2200 Hz audio.\n      // REG3 below is specific to the qualified 14.7456 MHz simplex-HAT target:\n      // DEMOD_CLK 4.9152 MHz / CDR_DIVIDE 8 / 32 = 19.2 kbit/s.\n      ADF7021_REG3 = 0x2A4C20D3U;\n      ADF7021_REG10 = ADF7021_REG10_DMR;\n\n      ADF7021_REG4  = (uint32_t) 0b0100                    << 0;   // register 4\n      ADF7021_REG4 |= (uint32_t) 0b000                     << 4;   // 2FSK linear demodulator\n      ADF7021_REG4 |= (uint32_t) 0b0                       << 7;\n      ADF7021_REG4 |= (uint32_t) 0b11                      << 8;\n      ADF7021_REG4 |= (uint32_t) ADF7021_DISC_BW_DMR       << 10;\n      ADF7021_REG4 |= (uint32_t) ADF7021_POST_BW_DMR       << 20;\n      ADF7021_REG4 |= (uint32_t) 0b10                      << 30;  // IF filter 25 kHz\n\n      ADF7021_REG13 = (uint32_t) 0b1101                    << 0;   // unused for 2FSK\n\n      ADF7021_REG2  = (uint32_t) 0b00                      << 28;  // normal data/clock sense\n      ADF7021_REG2 |= (uint32_t) (107U / div2)             << 19;  // about +/-3.0 kHz at VHF\n      ADF7021_REG2 |= (uint32_t) 0b001                     << 4;   // Gaussian 2FSK\n      break;\n\n'''
    primary = replace_once(
        primary,
        "    case STATE_DMR:\n      // Dev: +1 symb 648 Hz, symb rate = 4800",
        ax25_adf_case + "    case STATE_DMR:\n      // Dev: +1 symb 648 Hz, symb rate = 4800",
        "classic AX25 ADF7021 mode",
    )

    if duplex_tail != original_duplex_tail:
        raise SystemExit("duplex CIO::ifConf2 changed unexpectedly")
    adf = primary + DUPLEX_BOUNDARY + duplex_tail

    serial = replace_once(
        serial,
        "const uint8_t MMDVM_YWD_DATA     = 0x57U;\n",
        "const uint8_t MMDVM_YWD_DATA     = 0x57U;\nconst uint8_t MMDVM_YWD_RF       = 0x58U;\n",
        "YWD_RF command",
    )
    serial = replace_once(
        serial,
        "const uint8_t YWD_KIND_BLOB      = 0x03U;\n",
        "const uint8_t YWD_KIND_BLOB      = 0x03U;\n\nconst uint8_t YWD_RF_GET_STATUS  = 0x01U;\nconst uint8_t YWD_RF_TX_TONES    = 0x02U;\nconst uint8_t YWD_RF_ABORT       = 0x03U;\nconst uint8_t YWD_RF_EXIT        = 0x04U;\nconst uint16_t YWD_RF_MAX_TONES  = 1920U;\n\nstatic bool m_ywdRFReady = false;\n",
        "classic RF subcommands",
    )
    serial = replace_once(
        serial,
        "              reply[6U] = 0x07U;         // control + data echo + legacy ping",
        "              reply[6U] = 0x0FU;         // control + data echo + legacy ping + explicit RF engine",
        "classic RF capability bit",
    )
    serial = replace_once(
        serial,
        '              const char info[] = "YWD-MMDVM-STAGE4";',
        '              const char info[] = "YWD-MMDVM-AX25C1";',
        "classic AX25 info string",
    )
    serial = replace_once(
        serial,
        "  io.start();\n#if defined(ENABLE_DEBUG)",
        "  io.start();\n  m_ywdRFReady = true;\n#if defined(ENABLE_DEBUG)",
        "RF ready after normal SET_CONFIG",
    )

    rf_case = r'''          case MMDVM_YWD_RF: {
            // Classic AX25-1 RF namespace. Ordinary YWD_DATA never reaches RF.
            if (m_len < 4U) {
              sendNAK(4U);
              break;
            }

            const uint8_t sub = m_buffer[3U];
            if (sub == YWD_RF_GET_STATUS) {
              if (m_len != 4U) {
                sendNAK(4U);
                break;
              }

              uint8_t flags = 0U;
              if (ax25AFSKTX.busy())
                flags |= 0x01U;
              if (m_tx)
                flags |= 0x02U;
              if (m_modemState == STATE_AX25)
                flags |= 0x04U;
              if (m_ywdRFReady)
                flags |= 0x08U;

              const uint16_t remaining = ax25AFSKTX.remaining();
              uint8_t reply[9U];
              reply[0U] = MMDVM_FRAME_START;
              reply[1U] = 9U;
              reply[2U] = MMDVM_YWD_RF;
              reply[3U] = YWD_RF_GET_STATUS;
              reply[4U] = 1U; // classic AX25 RF engine protocol revision
              reply[5U] = flags;
              reply[6U] = uint8_t(remaining & 0xFFU);
              reply[7U] = uint8_t((remaining >> 8) & 0xFFU);
              reply[8U] = uint8_t(m_modemState);
              writeInt(1U, reply, 9);
            } else if (sub == YWD_RF_TX_TONES) {
              if (m_len < 7U || !m_ywdRFReady) {
                sendNAK(m_ywdRFReady ? 4U : 5U);
                break;
              }

              const uint16_t bitCount = uint16_t(m_buffer[4U]) | (uint16_t(m_buffer[5U]) << 8);
              const uint16_t packedLen = uint16_t((bitCount + 7U) / 8U);
              if (bitCount == 0U || bitCount > YWD_RF_MAX_TONES ||
                  packedLen > 240U || m_len != uint8_t(6U + packedLen)) {
                sendNAK(4U);
                break;
              }
              if (m_modemState != STATE_IDLE && m_modemState != STATE_AX25) {
                sendNAK(5U);
                break;
              }

              const bool entered = m_modemState == STATE_IDLE;
              if (entered)
                setMode(STATE_AX25);

              const uint8_t err = ax25AFSKTX.writeSelectors(m_buffer + 6U, uint8_t(packedLen), bitCount);
              if (err == 0U) {
                sendACK();
              } else {
                if (entered && !m_tx)
                  setMode(STATE_IDLE);
                sendNAK(err);
              }
            } else if (sub == YWD_RF_ABORT) {
              if (m_len != 4U) {
                sendNAK(4U);
                break;
              }
              ax25AFSKTX.abort();
              sendACK();
            } else if (sub == YWD_RF_EXIT) {
              if (m_len != 4U) {
                sendNAK(4U);
                break;
              }
              if (ax25AFSKTX.busy() || m_tx) {
                sendNAK(5U);
                break;
              }
              ax25AFSKTX.abort();
              setMode(STATE_IDLE);
              sendACK();
            } else {
              sendNAK(4U);
            }
            break;
          }

'''
    serial = replace_once(
        serial,
        "            writeInt(1U, reply, m_len);\n            break;\n          }\n\n          case MMDVM_GET_STATUS:",
        "            writeInt(1U, reply, m_len);\n            break;\n          }\n\n" + rf_case + "          case MMDVM_GET_STATUS:",
        "classic YWD_RF switch case",
    )

    ax25_mode_case = '''    case STATE_AX25:\n      DEBUG1("Mode set to YWD classic AX25 1200");\n#if defined(DUPLEX)\n      dmrIdleRX.reset();\n      dmrRX.reset();\n#endif\n      dmrDMORX.reset();\n      dstarRX.reset();\n      ysfRX.reset();\n      p25RX.reset();\n      nxdnRX.reset();\n      m17RX.reset();\n      cwIdTX.reset();\n      break;\n'''
    serial = replace_once(
        serial,
        "    case STATE_POCSAG:\n      DEBUG1(\"Mode set to POCSAG\");",
        ax25_mode_case + "    case STATE_POCSAG:\n      DEBUG1(\"Mode set to POCSAG\");",
        "classic AX25 mode setup",
    )

    version = replace_once(
        version,
        '#define DESCRIPTION     BOARD_INFO "-YWD-STAGE4-v0.0.3 " TCXO_FREQ "MHz " RF_DUAL RF_CHIP " FW based on CA6JAU"',
        '#define DESCRIPTION     BOARD_INFO "-YWD-AX25C1-v0.1.0 " TCXO_FREQ "MHz " RF_DUAL RF_CHIP " FW based on CA6JAU"',
        "classic AX25 firmware identity",
    )

    globals_path.write_text(globals_h)
    main_path.write_text(main_cpp)
    adf_path.write_text(adf)
    serial_path.write_text(serial)
    version_path.write_text(version)

    shutil.copyfile(here / "AX25AFSKTX.h", src / "AX25AFSKTX.h")
    shutil.copyfile(here / "AX25AFSKTX.cpp", src / "AX25AFSKTX.cpp")

    tracked = git_lines(src, "diff", "--name-only")
    untracked = git_lines(src, "ls-files", "--others", "--exclude-standard")
    if tracked != EXPECTED_FINAL_TRACKED:
        raise SystemExit(f"unexpected tracked firmware files changed: {tracked}")
    if untracked != EXPECTED_UNTRACKED:
        raise SystemExit(f"unexpected generated firmware files: {untracked}")
    if (src / "Config.h").read_bytes() != (src / "configs/MMDVM_HS_Hat.h").read_bytes():
        raise SystemExit("Config.h changed during classic AX25 transform")
    if duplex_tail != original_duplex_tail:
        raise SystemExit("duplex ADF7021 configuration changed unexpectedly")

    subprocess.check_call(["git", "-C", str(src), "diff", "--check"])
    print("AX25_CLASSIC1_SOURCE_TRANSFORM=PASS")
    print("ADF7021 scope: primary CIO::ifConf() only; DUPLEX ifConf2() unchanged")
    print("PHY experiment: 19.2 kbps Gaussian 2FSK used as 1-bit Bell-202 waveform DAC")
    print("Bell-202 selectors: 1200 baud; 0=1200 Hz MARK, 1=2200 Hz SPACE")
    print("RF command: 0x58; maximum burst: 1920 tone selectors")
    print("normal YWD_DATA remains host-only and cannot key RF")


if __name__ == "__main__":
    main()
