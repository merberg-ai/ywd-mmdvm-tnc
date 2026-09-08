#!/usr/bin/env python3
"""Layer AX25R2 CDR-bypass linear-slicer capture onto AX25R1 v0.2.0.

AX25R1 sampled TxRxDATA using the ADF7021 recovered TxRxCLK.  That data has
already passed through the ADF7021 clock/data recovery path.  AX25R2 instead
uses ADF7021 Register 15 RX test mode 6 (linear slicer on TxRxDATA, CDR
bypassed) and samples PB4 from a local STM32 TIM2 interrupt at 19.2 ksample/s.

The CDR bypass is active only while the passive YWD_RX capture is running.
Normal Register 15 configuration is restored on STOP, and the qualified
AX25C1 v0.1.4 transmit path is otherwise unchanged.
"""
from pathlib import Path
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
EXPECTED_BEFORE_UNTRACKED = [
    "AX25AFSKRX.cpp",
    "AX25AFSKRX.h",
    "AX25AFSKTX.cpp",
    "AX25AFSKTX.h",
]
EXPECTED_AFTER_TRACKED = [
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
        raise SystemExit("usage: apply_ax25_rx2.py PATH_TO_MMDVM_HS")

    src = Path(sys.argv[1]).resolve()
    if git_lines(src, "diff", "--name-only") != sorted(EXPECTED_BEFORE_TRACKED):
        raise SystemExit("AX25 RX2 requires the exact deterministic AX25R1 v0.2.0 transformed tree")
    if git_lines(src, "ls-files", "--others", "--exclude-standard") != sorted(EXPECTED_BEFORE_UNTRACKED):
        raise SystemExit("unexpected generated files before AX25 RX2 transform")
    if (src / "Config.h").read_bytes() != (src / "configs/MMDVM_HS_Hat.h").read_bytes():
        raise SystemExit("Config.h no longer exactly matches pinned simplex-HAT configuration")

    adf_path = src / "ADF7021.cpp"
    io_h_path = src / "IO.h"
    iostm_path = src / "IOSTM.cpp"
    serial_path = src / "SerialPort.cpp"
    version_path = src / "version.h"

    adf = adf_path.read_text()
    io_h = io_h_path.read_text()
    iostm = iostm_path.read_text()
    serial = serial_path.read_text()
    version = version_path.read_text()

    if "YWD-AX25R1-v0.2.0" not in version:
        raise SystemExit("AX25R1 v0.2.0 identity is missing")
    if "ax25AFSKRX.sample(bit)" not in adf:
        raise SystemExit("AX25R1 recovered-clock RX tap is missing")
    if "CIO_FIFO_RESERVE = 256U" not in (src / "AX25AFSKTX.cpp").read_text():
        raise SystemExit("qualified AX25C1 v0.1.4 TX reserve is missing")

    # The ADF7021 normal RX/TX interface uses Register 15 value 0x000E000F.
    # RX test mode 6 changes DB7:DB4 to 0b0110, selecting the linear slicer on
    # TxRxDATA while bypassing CDR.  Restore the normal value after capture.
    adf = replace_once(
        adf,
        "#if defined(SEND_RSSI_DATA)\nuint16_t CIO::readRSSI()",
        "void CIO::setAX25RawSlicer(bool enable)\n"
        "{\n"
        "  AD7021_control_word = enable ? 0x000E006FU : 0x000E000FU;\n"
        "  Send_AD7021_control();\n"
        "}\n\n"
        "#if defined(SEND_RSSI_DATA)\nuint16_t CIO::readRSSI()",
        "AX25 Register-15 CDR-bypass helper",
    )

    adf = replace_once(
        adf,
        "    if (m_modemState == STATE_AX25)\n"
        "      ax25AFSKRX.sample(bit);\n"
        "    else\n"
        "      m_rxBuffer.put(bit, m_control);\n",
        "    // AX25R2 capture is sampled by local TIM2 from pre-CDR TxRxDATA.\n"
        "    // Ignore the ADF recovered-clock data path while STATE_AX25 is active.\n"
        "    if (m_modemState != STATE_AX25)\n"
        "      m_rxBuffer.put(bit, m_control);\n",
        "remove recovered-clock AX25R1 RX tap",
    )

    io_h = replace_once(
        io_h,
        "  void      interrupt(void);\n",
        "  void      interrupt(void);\n"
        "  void      startAX25SampleTimer(void);\n"
        "  void      stopAX25SampleTimer(void);\n"
        "  void      ax25SampleTimerInterrupt(void);\n"
        "  void      setAX25RawSlicer(bool enable);\n",
        "AX25R2 IO API",
    )

    iostm = replace_once(
        iostm,
        'extern "C" {\n',
        'extern "C" {\n'
        '  void TIM2_IRQHandler(void) {\n'
        '    if ((TIM2->SR & TIM_SR_UIF) != 0U) {\n'
        '      TIM2->SR &= ~TIM_SR_UIF;\n'
        '      io.ax25SampleTimerInterrupt();\n'
        '    }\n'
        '  }\n\n',
        "TIM2 ISR",
    )

    timer_methods = r'''void CIO::startAX25SampleTimer(void)
{
  RCC_ClocksTypeDef clocks;
  RCC_GetClocksFreq(&clocks);

  // STM32F1 timers run at 2*PCLK when the APB prescaler is not /1.
  uint32_t timerClock = clocks.PCLK1_Frequency;
  if ((RCC->CFGR & 0x00000700U) != 0U)
    timerClock *= 2U;

  const uint32_t divisor = timerClock / 19200U;
  if (divisor == 0U || (timerClock % 19200U) != 0U)
    return;

  // TxRxCLK belongs to the ADF7021 CDR.  It is deliberately not our sample
  // clock in RX2, so mask EXTI3 for the duration of passive raw capture.
  EXTI->IMR &= ~EXTI_Line3;
  EXTI_ClearITPendingBit(EXTI_Line3);

  RCC_APB1PeriphClockCmd(RCC_APB1Periph_TIM2, ENABLE);
  TIM2->CR1 = 0U;
  TIM2->DIER = 0U;
  TIM2->PSC = 0U;
  TIM2->ARR = divisor - 1U;
  TIM2->CNT = 0U;
  TIM2->EGR = TIM_EGR_UG;
  TIM2->SR = 0U;
  TIM2->DIER = TIM_DIER_UIE;

  NVIC_ClearPendingIRQ(TIM2_IRQn);
  NVIC_SetPriority(TIM2_IRQn, 1U);
  NVIC_EnableIRQ(TIM2_IRQn);
  TIM2->CR1 = TIM_CR1_CEN;
}

void CIO::stopAX25SampleTimer(void)
{
  TIM2->CR1 = 0U;
  TIM2->DIER = 0U;
  TIM2->SR = 0U;
  NVIC_DisableIRQ(TIM2_IRQn);
  NVIC_ClearPendingIRQ(TIM2_IRQn);
  RCC_APB1PeriphClockCmd(RCC_APB1Periph_TIM2, DISABLE);

  EXTI_ClearITPendingBit(EXTI_Line3);
  EXTI->IMR |= EXTI_Line3;
}

void CIO::ax25SampleTimerInterrupt(void)
{
  if (m_modemState == STATE_AX25 && ax25AFSKRX.active())
    ax25AFSKRX.sample(RXD_pin() ? 1U : 0U);
}

'''
    iostm = replace_once(
        iostm,
        "void CIO::Init()\n{",
        timer_methods + "void CIO::Init()\n{",
        "AX25R2 local sample timer methods",
    )

    serial = replace_once(
        serial,
        "              io.ifConf(STATE_AX25, true);\n"
        "              setMode(STATE_AX25);\n"
        "              ax25AFSKRX.start();\n"
        "              sendACK();",
        "              io.ifConf(STATE_AX25, true);\n"
        "              setMode(STATE_AX25);\n"
        "              io.setAX25RawSlicer(true);\n"
        "              ax25AFSKRX.start();\n"
        "              io.startAX25SampleTimer();\n"
        "              sendACK();",
        "RX2 START CDR bypass + timer",
    )

    serial = replace_once(
        serial,
        "              ax25AFSKRX.stop();\n"
        "              if (m_modemState == STATE_AX25 && !m_tx && !ax25AFSKTX.busy())\n"
        "                setMode(STATE_IDLE);\n"
        "              sendACK();",
        "              if (ax25AFSKRX.active()) {\n"
        "                io.stopAX25SampleTimer();\n"
        "                ax25AFSKRX.stop();\n"
        "                io.setAX25RawSlicer(false);\n"
        "              }\n"
        "              if (m_modemState == STATE_AX25 && !m_tx && !ax25AFSKTX.busy())\n"
        "                setMode(STATE_IDLE);\n"
        "              sendACK();",
        "RX2 STOP restore normal ADF path",
    )

    serial = replace_once(
        serial,
        "              reply[4U] = 1U; // raw RX capture protocol revision",
        "              reply[4U] = 2U; // RX2: CDR-bypass local-timer capture revision",
        "RX2 protocol revision",
    )
    serial = replace_once(
        serial,
        '              const char info[] = "YWD-MMDVM-AX25R1";',
        '              const char info[] = "YWD-MMDVM-AX25R2";',
        "AX25R2 info string",
    )
    version = replace_once(
        version,
        "YWD-AX25R1-v0.2.0",
        "YWD-AX25R2-v0.2.1",
        "AX25R2 firmware identity",
    )

    adf_path.write_text(adf)
    io_h_path.write_text(io_h)
    iostm_path.write_text(iostm)
    serial_path.write_text(serial)
    version_path.write_text(version)

    if git_lines(src, "diff", "--name-only") != sorted(EXPECTED_AFTER_TRACKED):
        raise SystemExit("unexpected tracked firmware diff after AX25 RX2 transform")
    if git_lines(src, "ls-files", "--others", "--exclude-standard") != sorted(EXPECTED_BEFORE_UNTRACKED):
        raise SystemExit("unexpected generated files after AX25 RX2 transform")
    if (src / "Config.h").read_bytes() != (src / "configs/MMDVM_HS_Hat.h").read_bytes():
        raise SystemExit("Config.h changed during AX25 RX2 transform")

    subprocess.check_call(["git", "-C", str(src), "diff", "--check"])
    print("AX25_RX2_SOURCE_TRANSFORM=PASS")
    print("ADF7021 Register 15: mode 6 linear slicer on TxRxDATA; CDR bypassed during capture")
    print("sample clock: STM32 TIM2 derived from actual APB1 timer clock; exact 19.2 ksample/s required")
    print("TxRxCLK/EXTI3: masked only during passive raw capture; restored on STOP")
    print("normal Register 15: restored to 0x000E000F on STOP")
    print("qualified AX25C1 v0.1.4 transmit path otherwise unchanged")
    print("identity: YWD-AX25R2-v0.2.1")


if __name__ == "__main__":
    main()
