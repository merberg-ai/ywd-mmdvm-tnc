#!/usr/bin/env python3
"""Apply the YWD-MMDVM Stage 4 host-protocol changes to exact upstream 7ff74ed.

This intentionally changes Stage 4 behavior only in SerialPort.cpp and version.h.
The build checkout also generates Config.h by copying configs/MMDVM_HS_Hat.h;
that generated target configuration is allowed only when it is byte-identical to
the pinned simplex-HAT template.
"""
from pathlib import Path
import subprocess
import sys

EXPECTED_SERIAL_BLOB = "1274b3455963ea129b94ae5bc34d4c7935947562"
EXPECTED_VERSION_BLOB = "4239a854ec09ee90847468f931e1455ee461e2de"


def git_blob(src: Path, rel: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(src), "hash-object", rel], text=True
    ).strip()


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one pristine anchor, found {count}")
    return text.replace(old, new, 1)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: apply_stage4.py PATH_TO_MMDVM_HS")

    src = Path(sys.argv[1]).resolve()
    serial_path = src / "SerialPort.cpp"
    version_path = src / "version.h"
    config_path = src / "Config.h"
    config_template = src / "configs" / "MMDVM_HS_Hat.h"

    if git_blob(src, "SerialPort.cpp") != EXPECTED_SERIAL_BLOB:
        raise SystemExit("SerialPort.cpp blob does not match pinned 7ff74ed source")
    if git_blob(src, "version.h") != EXPECTED_VERSION_BLOB:
        raise SystemExit("version.h blob does not match pinned 7ff74ed source")

    # checkout_source() intentionally creates Config.h for the target board.
    # Treat it as build configuration, not a Stage 4 behavioral edit, but only
    # when it is an exact copy of the pinned simplex MMDVM_HS_Hat template.
    if not config_path.is_file() or not config_template.is_file():
        raise SystemExit("expected simplex-HAT Config.h/template is missing")
    if config_path.read_bytes() != config_template.read_bytes():
        raise SystemExit("Config.h differs from pinned configs/MMDVM_HS_Hat.h; refusing transform")

    serial = serial_path.read_text()
    version = version_path.read_text()

    constants_old = """const uint8_t MMDVM_POCSAG_DATA  = 0x50U;\n\nconst uint8_t MMDVM_ACK          = 0x70U;\n"""
    constants_new = """const uint8_t MMDVM_POCSAG_DATA  = 0x50U;\n\n// YWD-MMDVM host extensions. Stage 4 remains host-UART only; no RF path uses\n// these command bytes. 0x56 keeps Stage 3 legacy PING compatibility while\n// adding structured control subcommands. 0x57 is a bounded binary-safe echo\n// transport used to qualify framing before RF is introduced.\nconst uint8_t MMDVM_YWD_CONTROL  = 0x56U;\nconst uint8_t MMDVM_YWD_DATA     = 0x57U;\n\nconst uint8_t YWD_CTRL_PING      = 0x01U;\nconst uint8_t YWD_CTRL_GET_CAPS  = 0x02U;\nconst uint8_t YWD_CTRL_GET_INFO  = 0x03U;\n\nconst uint8_t YWD_DATA_MAX       = 32U;\nconst uint8_t YWD_KIND_TEXT      = 0x01U;\nconst uint8_t YWD_KIND_TELEMETRY = 0x02U;\nconst uint8_t YWD_KIND_BLOB      = 0x03U;\n\nconst uint8_t MMDVM_ACK          = 0x70U;\n"""
    serial = replace_once(serial, constants_old, constants_new, "command constants")

    switch_old = """        switch (m_buffer[2U]) {\n          case MMDVM_GET_STATUS:\n"""
    switch_new = r'''        switch (m_buffer[2U]) {
          case MMDVM_YWD_CONTROL: {
            // Stage 3 compatibility: an empty 0x56 request still returns the
            // original unstructured PONG frame.
            if (m_len == 3U) {
              uint8_t reply[7U];
              reply[0U] = MMDVM_FRAME_START;
              reply[1U] = 7U;
              reply[2U] = MMDVM_YWD_CONTROL;
              reply[3U] = 'P';
              reply[4U] = 'O';
              reply[5U] = 'N';
              reply[6U] = 'G';
              writeInt(1U, reply, 7);
              break;
            }

            if (m_len != 4U) {
              sendNAK(4U);
              break;
            }

            const uint8_t sub = m_buffer[3U];
            if (sub == YWD_CTRL_PING) {
              uint8_t reply[8U];
              reply[0U] = MMDVM_FRAME_START;
              reply[1U] = 8U;
              reply[2U] = MMDVM_YWD_CONTROL;
              reply[3U] = YWD_CTRL_PING;
              reply[4U] = 'P';
              reply[5U] = 'O';
              reply[6U] = 'N';
              reply[7U] = 'G';
              writeInt(1U, reply, 8);
            } else if (sub == YWD_CTRL_GET_CAPS) {
              uint8_t reply[9U];
              reply[0U] = MMDVM_FRAME_START;
              reply[1U] = 9U;
              reply[2U] = MMDVM_YWD_CONTROL;
              reply[3U] = YWD_CTRL_GET_CAPS;
              reply[4U] = 1U;            // YWD host protocol revision
              reply[5U] = YWD_DATA_MAX;  // maximum Stage 4 data payload
              reply[6U] = 0x07U;         // control + data echo + legacy ping
              reply[7U] = 0x07U;         // TEXT + TELEMETRY + BLOB kinds
              reply[8U] = 0x00U;         // reserved
              writeInt(1U, reply, 9);
            } else if (sub == YWD_CTRL_GET_INFO) {
              const char info[] = "YWD-MMDVM-STAGE4";
              uint8_t reply[20U];
              reply[0U] = MMDVM_FRAME_START;
              reply[1U] = 20U;
              reply[2U] = MMDVM_YWD_CONTROL;
              reply[3U] = YWD_CTRL_GET_INFO;
              for (uint8_t i = 0U; i < 16U; i++)
                reply[4U + i] = uint8_t(info[i]);
              writeInt(1U, reply, 20);
            } else {
              sendNAK(4U);
            }
            break;
          }

          case MMDVM_YWD_DATA: {
            // E0 LEN 57 SEQ KIND PAYLOAD_LEN PAYLOAD...
            if (m_len < 6U) {
              sendNAK(4U);
              break;
            }

            const uint8_t kind = m_buffer[4U];
            const uint8_t payloadLen = m_buffer[5U];
            if (payloadLen > YWD_DATA_MAX || m_len != uint8_t(6U + payloadLen) ||
                kind < YWD_KIND_TEXT || kind > YWD_KIND_BLOB) {
              sendNAK(4U);
              break;
            }

            // Stage 4 deliberately echoes a validated frame unchanged. This
            // proves binary-safe bounded transport without touching RF state.
            uint8_t reply[38U];
            reply[0U] = MMDVM_FRAME_START;
            reply[1U] = m_len;
            reply[2U] = MMDVM_YWD_DATA;
            reply[3U] = m_buffer[3U];
            reply[4U] = kind;
            reply[5U] = payloadLen;
            for (uint8_t i = 0U; i < payloadLen; i++)
              reply[6U + i] = m_buffer[6U + i];
            writeInt(1U, reply, m_len);
            break;
          }

          case MMDVM_GET_STATUS:
'''
    serial = replace_once(serial, switch_old, switch_new, "serial command switch")

    version_old = '#define DESCRIPTION     BOARD_INFO "-" FW_VERSION " " TCXO_FREQ "MHz " RF_DUAL RF_CHIP " FW by CA6JAU"'
    version_new = '#define DESCRIPTION     BOARD_INFO "-YWD-STAGE4-v0.0.3 " TCXO_FREQ "MHz " RF_DUAL RF_CHIP " FW based on CA6JAU"'
    version = replace_once(version, version_old, version_new, "firmware identity")

    serial_path.write_text(serial)
    version_path.write_text(version)

    changed = subprocess.check_output(
        ["git", "-C", str(src), "diff", "--name-only"], text=True
    ).splitlines()
    expected = ["Config.h", "SerialPort.cpp", "version.h"]
    if changed != expected:
        raise SystemExit(f"unexpected firmware files changed: {changed}")

    # Re-check after the transformation so a future edit cannot piggyback on
    # the allowed generated Config.h entry.
    if config_path.read_bytes() != config_template.read_bytes():
        raise SystemExit("Config.h changed during Stage 4 transform")

    subprocess.check_call(["git", "-C", str(src), "diff", "--check"])
    print("STAGE4_SOURCE_PATCH=PASS")
    print("generated config: Config.h == configs/MMDVM_HS_Hat.h")
    print("behavioral changes: SerialPort.cpp, version.h")


if __name__ == "__main__":
    main()
