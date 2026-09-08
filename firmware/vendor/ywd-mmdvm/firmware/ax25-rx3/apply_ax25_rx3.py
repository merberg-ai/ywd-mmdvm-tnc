#!/usr/bin/env python3
"""Layer AX25R3 Bell-202 post-demod filtering onto AX25R2 v0.2.1.

AX25R2 proved the CDR-bypass / local-TIM2 capture path works, but its first
physical capture was much noisier than the recovered-clock experiment and did
not yield an FCS-valid frame.  The AX25 state still inherited the upstream DMR
post-demodulator setting (POST_DEMOD_BW=80).  With the qualified 4.9152 MHz
demodulator clock this is roughly a 61 kHz cutoff, far wider than necessary to
preserve 1200/2200 Hz Bell-202.

RX3 changes only the AX25 receive post-demodulator field from 80 to 5.  That is
approximately a 3.82 kHz cutoff at the same demodulator clock.  The qualified
AX25 TX timing/deviation/NCO, the RX2 Register-15 CDR bypass, TIM2 sampler,
FIFO, and all non-AX25 modem paths are otherwise unchanged.
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
        raise SystemExit("usage: apply_ax25_rx3.py PATH_TO_MMDVM_HS")

    src = Path(sys.argv[1]).resolve()
    if git_lines(src, "diff", "--name-only") != sorted(EXPECTED_TRACKED):
        raise SystemExit("AX25 RX3 requires the exact deterministic AX25R2 v0.2.1 transformed tree")
    if git_lines(src, "ls-files", "--others", "--exclude-standard") != sorted(EXPECTED_UNTRACKED):
        raise SystemExit("unexpected generated files before AX25 RX3 transform")
    if (src / "Config.h").read_bytes() != (src / "configs/MMDVM_HS_Hat.h").read_bytes():
        raise SystemExit("Config.h no longer exactly matches pinned simplex-HAT configuration")

    adf_path = src / "ADF7021.cpp"
    serial_path = src / "SerialPort.cpp"
    version_path = src / "version.h"

    adf = adf_path.read_text()
    serial = serial_path.read_text()
    version = version_path.read_text()

    if "YWD-AX25R2-v0.2.1" not in version:
        raise SystemExit("AX25R2 v0.2.1 identity is missing")
    if "0x000E006FU" not in adf or "TIM2" not in (src / "IOSTM.cpp").read_text():
        raise SystemExit("AX25R2 CDR-bypass / local-timer receive layer is missing")
    if "CIO_FIFO_RESERVE = 256U" not in (src / "AX25AFSKTX.cpp").read_text():
        raise SystemExit("qualified AX25C1 v0.1.4 TX reserve is missing")

    old_filter = (
        "      ADF7021_REG4 |= (uint32_t) ADF7021_DISC_BW_DMR       << 10;\n"
        "      ADF7021_REG4 |= (uint32_t) ADF7021_POST_BW_DMR       << 20;\n"
        "      ADF7021_REG4 |= (uint32_t) 0b10                      << 30;  // IF filter 25 kHz\n\n"
        "      ADF7021_REG13 = (uint32_t) 0b1101                    << 0;   // unused for 2FSK"
    )
    new_filter = (
        "      ADF7021_REG4 |= (uint32_t) ADF7021_DISC_BW_DMR       << 10;\n"
        "      ADF7021_REG4 |= (uint32_t) 5U                         << 20;  // RX3: ~3.82 kHz post-demod cutoff\n"
        "      ADF7021_REG4 |= (uint32_t) 0b10                      << 30;  // IF filter 25 kHz\n\n"
        "      ADF7021_REG13 = (uint32_t) 0b1101                    << 0;   // unused for 2FSK"
    )
    adf = replace_once(adf, old_filter, new_filter, "AX25-only post-demod filter")

    serial = replace_once(
        serial,
        "              reply[4U] = 2U; // RX2: CDR-bypass local-timer capture revision",
        "              reply[4U] = 3U; // RX3: CDR-bypass + Bell-202 post-demod filter revision",
        "RX3 protocol revision",
    )
    serial = replace_once(
        serial,
        '              const char info[] = "YWD-MMDVM-AX25R2";',
        '              const char info[] = "YWD-MMDVM-AX25R3";',
        "AX25R3 info string",
    )
    version = replace_once(
        version,
        "YWD-AX25R2-v0.2.1",
        "YWD-AX25R3-v0.2.2",
        "AX25R3 firmware identity",
    )

    adf_path.write_text(adf)
    serial_path.write_text(serial)
    version_path.write_text(version)

    if git_lines(src, "diff", "--name-only") != sorted(EXPECTED_TRACKED):
        raise SystemExit("unexpected tracked firmware diff after AX25 RX3 transform")
    if git_lines(src, "ls-files", "--others", "--exclude-standard") != sorted(EXPECTED_UNTRACKED):
        raise SystemExit("unexpected generated files after AX25 RX3 transform")
    if (src / "Config.h").read_bytes() != (src / "configs/MMDVM_HS_Hat.h").read_bytes():
        raise SystemExit("Config.h changed during AX25 RX3 transform")

    subprocess.check_call(["git", "-C", str(src), "diff", "--check"])
    print("AX25_RX3_SOURCE_TRANSFORM=PASS")
    print("ADF7021 AX25 post-demod field: 80 -> 5")
    print("estimated post-demod cutoff: ~61.1 kHz -> ~3.82 kHz at DEMOD_CLK=4.9152 MHz")
    print("ADF7021 discriminator bandwidth remains K=32 / ADF7021_DISC_BW_DMR")
    print("RX2 Register-15 CDR bypass + TIM2 19.2ksps sampler unchanged")
    print("qualified AX25C1 TX timing/deviation/NCO unchanged")
    print("identity: YWD-AX25R3-v0.2.2")


if __name__ == "__main__":
    main()
