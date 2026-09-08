#!/usr/bin/env python3
"""Keep Classic-1 TX continuous across brief CIO refill gaps.

Applied after the base AX25C1 and diagnostic transforms.  This does not change
the Bell-202 NCO, ADF7021 clock, deviation, or host framing.  It changes only
scheduler/refill behavior so the stock CIO empty-FIFO check cannot repeatedly
drop PTT while the AX25 waveform engine still has samples left to generate.

It also replaces the generic 16-bit INT1 diagnostic with a dedicated bounded
AX25 transmit-sample counter.  Classic-1 currently permits at most 30,720
waveform samples per burst, so this counter cannot wrap during a legal burst
and can be used to measure the actual ADF7021 TX sample clock reliably.
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
        raise SystemExit("usage: apply_ax25_classic1_continuity.py PATH_TO_MMDVM_HS")

    src = Path(sys.argv[1]).resolve()
    io_path = src / "IO.cpp"
    main_path = src / "MMDVM_HS.cpp"
    adf_path = src / "ADF7021.cpp"
    serial_path = src / "SerialPort.cpp"
    version_path = src / "version.h"

    io_cpp = io_path.read_text()
    main_cpp = main_path.read_text()
    adf = adf_path.read_text()
    serial = serial_path.read_text()
    version = version_path.read_text()

    if "YWD-AX25C1-v0.1.2" not in version:
        raise SystemExit("Classic-1 v0.1.2 identity is missing before continuity transform")
    if "ax25AFSKTX.process();" not in main_cpp:
        raise SystemExit("Classic-1 TX scheduler is missing")
    if "YWD_RF_GET_DIAG" not in serial:
        raise SystemExit("Classic-1 RF diagnostics are missing")

    old_drop = '''  // Switch off the transmitter if needed
  if (m_txBuffer.getData() == 0U && m_tx) {
    if(m_cwid_state) { // check for CW ID end of transmission
      m_cwid_state = false;
      // Restoring previous mode
      if (m_TotalModes)
        io.ifConf(m_modemState_prev, true);
    }
    if(m_pocsag_state) { // check for POCSAG end of transmission
      m_pocsag_state = false;
      // Restoring previous mode
      if (m_TotalModes)
        io.ifConf(m_modemState_prev, true);
    }
    setRX(false);
  }
'''
    new_drop = '''  // Switch off the transmitter if needed.  Classic AX25 is generated in
  // bounded refill blocks, so a momentarily empty CIO ring is not end-of-burst
  // while the AX25 engine still has waveform samples left to generate.
  if (m_txBuffer.getData() == 0U && m_tx) {
    const bool ywdAX25RefillPending = (m_modemState == STATE_AX25 && ax25AFSKTX.busy());
    if (!ywdAX25RefillPending) {
      if(m_cwid_state) { // check for CW ID end of transmission
        m_cwid_state = false;
        // Restoring previous mode
        if (m_TotalModes)
          io.ifConf(m_modemState_prev, true);
      }
      if(m_pocsag_state) { // check for POCSAG end of transmission
        m_pocsag_state = false;
        // Restoring previous mode
        if (m_TotalModes)
          io.ifConf(m_modemState_prev, true);
      }
      setRX(false);
    }
  }
'''
    io_cpp = replace_once(io_cpp, old_drop, new_drop, "AX25 PTT continuity guard")

    main_cpp = replace_once(
        main_cpp,
        "void loop()\n{\n  io.process();",
        "void loop()\n{\n  // Refill Classic-1 before stock CIO can interpret an empty ring as EOT.\n"
        "  if (m_modemState == STATE_AX25)\n"
        "    ax25AFSKTX.process();\n\n"
        "  io.process();",
        "pre-IO AX25 refill scheduler",
    )

    adf = replace_once(
        adf,
        "volatile uint32_t  AD7021_control_word;\n",
        "volatile uint32_t  AD7021_control_word;\n"
        "volatile uint16_t  m_ywdAx25SampleEdges = 0U;\n",
        "AX25 sample clock counter",
    )
    adf = replace_once(
        adf,
        "  if (m_tx && clk == 0U) {\n    m_txBuffer.get(bit, m_control);",
        "  if (m_tx && clk == 0U) {\n"
        "    if (m_modemState == STATE_AX25 && m_ywdAx25SampleEdges < 65535U)\n"
        "      m_ywdAx25SampleEdges++;\n"
        "    m_txBuffer.get(bit, m_control);",
        "AX25 sample clock increment",
    )

    serial = replace_once(
        serial,
        '#include "Globals.h"\n#include "version.h"',
        '#include "Globals.h"\nextern volatile uint16_t m_ywdAx25SampleEdges;\n#include "version.h"',
        "AX25 sample counter extern",
    )
    serial = replace_once(
        serial,
        "              uint16_t int1 = 0U;\n              uint16_t int2 = 0U;\n              io.getIntCounter(int1, int2);\n              const uint16_t samples = ax25AFSKTX.samplesQueued();",
        "              const uint16_t int1 = m_ywdAx25SampleEdges;\n              const uint16_t samples = ax25AFSKTX.samplesQueued();",
        "GET_DIAG sample clock source",
    )
    serial = replace_once(
        serial,
        "              if (err == 0U) {\n                sendACK();",
        "              if (err == 0U) {\n                m_ywdAx25SampleEdges = 0U;\n                sendACK();",
        "reset AX25 sample clock counter",
    )

    version = replace_once(
        version,
        "YWD-AX25C1-v0.1.2",
        "YWD-AX25C1-v0.1.3",
        "continuity firmware identity",
    )

    io_path.write_text(io_cpp)
    main_path.write_text(main_cpp)
    adf_path.write_text(adf)
    serial_path.write_text(serial)
    version_path.write_text(version)

    subprocess.check_call(["git", "-C", str(src), "diff", "--check"])
    print("AX25_CLASSIC1_CONTINUITY_TRANSFORM=PASS")
    print("scheduler: refill AX25 before CIO empty-FIFO EOT check")
    print("PTT guard: keep TX asserted while AX25 waveform refill remains pending")
    print("diagnostics: GET_DIAG INT1 field now counts actual AX25 TX sample clocks")
    print("Bell-202 NCO / ADF7021 clock / deviation unchanged")
    print("identity bumped to YWD-AX25C1-v0.1.3")


if __name__ == "__main__":
    main()
