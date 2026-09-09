"""Dependency-free AX.25 codec primitives for YWD-1278.

Ported from the frozen YWD-MMDVM packet foundation at
``d25180ad663d781b761c525d1e699e7b052d6214`` (`tools/ax25/ax25.py`).

This module intentionally contains only host-side AX.25 address/FCS/frame
logic. It does not open the modem UART, touch GPIO, configure RF, or transmit.
KISS stream framing and Bell-202 work live in later, separate modules.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

AX25_UI = 0x03
AX25_PID_NO_L3 = 0xF0

S_FRAME_NAMES = {
    0: "RR",
    1: "RNR",
    2: "REJ",
    3: "SREJ",
}

U_FRAME_NAMES = {
    0x03: "UI",
    0x0F: "DM",
    0x2F: "SABM",
    0x43: "DISC",
    0x63: "UA",
    0x6F: "SABME",
    0x87: "FRMR",
    0xAF: "XID",
    0xE3: "TEST",
}


@dataclass(frozen=True)
class Address:
    """One AX.25 callsign/SSID address plus C/H flag state."""

    callsign: str
    ssid: int = 0
    flag: bool = False

    @classmethod
    def parse(cls, text: str, *, flag: bool = False) -> "Address":
        value = text.strip().upper()
        if not value:
            raise ValueError("empty callsign")
        if "-" in value:
            call, ssid_text = value.rsplit("-", 1)
            if not ssid_text.isdigit():
                raise ValueError(f"invalid SSID in {text!r}")
            ssid = int(ssid_text)
        else:
            call, ssid = value, 0
        if not (1 <= len(call) <= 6):
            raise ValueError("AX.25 callsign must contain 1..6 characters")
        if not all(c.isalnum() for c in call):
            raise ValueError("AX.25 callsign must be alphanumeric")
        if not (0 <= ssid <= 15):
            raise ValueError("AX.25 SSID must be 0..15")
        return cls(call, ssid, flag)

    def __str__(self) -> str:
        return self.callsign if self.ssid == 0 else f"{self.callsign}-{self.ssid}"


def crc_x25(data: bytes) -> int:
    """Return the AX.25 FCS value (CRC-16/X-25) for *data*."""

    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0x8408
            else:
                crc >>= 1
    return crc ^ 0xFFFF


def append_fcs(data: bytes) -> bytes:
    """Append the little-endian AX.25 FCS to a frame body."""

    return data + crc_x25(data).to_bytes(2, "little")


def verify_fcs(frame: bytes) -> bool:
    """Return True when the final two bytes are the correct AX.25 FCS."""

    if len(frame) < 2:
        return False
    expected = int.from_bytes(frame[-2:], "little")
    return crc_x25(frame[:-2]) == expected


def encode_address(address: Address, *, last: bool) -> bytes:
    """Encode one seven-byte AX.25 shifted address field."""

    call = address.callsign.upper().ljust(6)
    out = bytearray((ord(c) << 1) & 0xFE for c in call)
    # AX.25 SSID octet: C/H bit, reserved bits set, four-bit SSID, extension.
    ssid = 0x60 | ((address.ssid & 0x0F) << 1)
    if address.flag:
        ssid |= 0x80
    if last:
        ssid |= 0x01
    out.append(ssid)
    return bytes(out)


def decode_address(field: bytes) -> tuple[Address, bool]:
    """Decode one seven-byte AX.25 address and return (address, last)."""

    if len(field) != 7:
        raise ValueError("AX.25 address field must be seven bytes")
    if any(byte & 0x01 for byte in field[:6]):
        raise ValueError("invalid AX.25 shifted callsign field")
    if field[6] & 0x60 != 0x60:
        raise ValueError("invalid AX.25 SSID reserved bits")

    chars = [chr((byte >> 1) & 0x7F) for byte in field[:6]]
    seen_space = False
    for char in chars:
        if char == " ":
            seen_space = True
            continue
        if seen_space or not char.isascii() or not char.isalnum() or char.upper() != char:
            raise ValueError("invalid AX.25 callsign characters")
    callsign = "".join(chars).rstrip()
    if not callsign:
        raise ValueError("empty AX.25 callsign")

    ssid = (field[6] >> 1) & 0x0F
    flag = bool(field[6] & 0x80)
    last = bool(field[6] & 0x01)
    return Address(callsign, ssid, flag), last


def _decode_addresses(data: bytes) -> tuple[list[Address], int]:
    addresses: list[Address] = []
    offset = 0
    while True:
        if offset + 7 > len(data):
            raise ValueError("truncated AX.25 address list")
        address, last = decode_address(data[offset : offset + 7])
        addresses.append(address)
        offset += 7
        if last:
            break
        if len(addresses) > 10:
            raise ValueError("unreasonably long AX.25 address list")
    if len(addresses) < 2:
        raise ValueError("AX.25 frame requires destination and source addresses")
    return addresses, offset


def build_ui_frame(
    *,
    source: Address,
    destination: Address,
    info: bytes,
    path: Sequence[Address] = (),
    pid: int = AX25_PID_NO_L3,
    include_fcs: bool = True,
) -> bytes:
    """Build a conventional modulo-8 AX.25 UI frame."""

    if not (0 <= pid <= 0xFF):
        raise ValueError("PID must fit in one byte")

    # Conventional command/response sense for an outbound UI command frame.
    dest = Address(destination.callsign, destination.ssid, True)
    src = Address(source.callsign, source.ssid, False)
    addresses = [dest, src, *path]

    frame = bytearray()
    for index, address in enumerate(addresses):
        frame.extend(encode_address(address, last=index == len(addresses) - 1))
    frame.extend((AX25_UI, pid))
    frame.extend(info)
    data = bytes(frame)
    return append_fcs(data) if include_fcs else data


def parse_frame(frame: bytes, *, has_fcs: bool = True) -> dict:
    """Parse a common one-octet-control modulo-8 AX.25 frame.

    Supported classes are I, supervisory RR/RNR/REJ/SREJ, and common
    unnumbered UI/SABM/DISC/UA/DM/SABME/FRMR/XID/TEST frames. I and UI frames
    expose a PID byte. Other S/U frames leave bytes following control in
    ``info`` without inventing a PID.
    """

    if has_fcs:
        if not verify_fcs(frame):
            raise ValueError("bad AX.25 FCS")
        data = frame[:-2]
    else:
        data = frame

    if len(data) < 15:
        raise ValueError("AX.25 frame too short")

    addresses, offset = _decode_addresses(data)
    if offset >= len(data):
        raise ValueError("missing AX.25 control field")

    control = data[offset]
    cursor = offset + 1
    parsed = {
        "destination": addresses[0],
        "source": addresses[1],
        "path": addresses[2:],
        "control": control,
        "frame_class": "",
        "frame_type": "",
        "poll_final": False,
        "ns": None,
        "nr": None,
        "pid": None,
        "info": b"",
    }

    if (control & 0x01) == 0:
        if cursor >= len(data):
            raise ValueError("I frame missing PID")
        parsed["frame_class"] = "I"
        parsed["frame_type"] = "I"
        parsed["ns"] = (control >> 1) & 0x07
        parsed["poll_final"] = bool(control & 0x10)
        parsed["nr"] = (control >> 5) & 0x07
        parsed["pid"] = data[cursor]
        parsed["info"] = data[cursor + 1 :]
        return parsed

    if (control & 0x03) == 0x01:
        code = (control >> 2) & 0x03
        parsed["frame_class"] = "S"
        parsed["frame_type"] = S_FRAME_NAMES[code]
        parsed["poll_final"] = bool(control & 0x10)
        parsed["nr"] = (control >> 5) & 0x07
        parsed["info"] = data[cursor:]
        return parsed

    # Unnumbered frame. Bit 4 is P/F and is excluded from the command code.
    u_code = control & 0xEF
    parsed["frame_class"] = "U"
    parsed["frame_type"] = U_FRAME_NAMES.get(u_code, f"U-0x{u_code:02X}")
    parsed["poll_final"] = bool(control & 0x10)
    if u_code == AX25_UI:
        if cursor >= len(data):
            raise ValueError("UI frame missing PID")
        parsed["pid"] = data[cursor]
        parsed["info"] = data[cursor + 1 :]
    else:
        parsed["info"] = data[cursor:]
    return parsed


def parse_ui_frame(frame: bytes, *, has_fcs: bool = True) -> dict:
    """Parse a frame and require it to be an AX.25 UI frame."""

    parsed = parse_frame(frame, has_fcs=has_fcs)
    if parsed["frame_type"] != "UI":
        raise ValueError(f"not an AX.25 UI frame: control=0x{parsed['control']:02X}")
    return parsed
