"""Pure YWD/MMDVM host-protocol encoding and decoding.

This module performs no device I/O. It preserves the wire opcodes used by the
physically-qualified YWD-MMDVM AX25R3 / AX25-5B boundary and adds only the
read-only AX25R4 RSSI telemetry primitive staged for 0C-P2.
"""

from __future__ import annotations

from dataclasses import dataclass

START = 0xE0
GET_VERSION = 0x00
SET_CONFIG = 0x02
SET_FREQ = 0x04
ACK = 0x70
NAK = 0x7F
YWD_CONTROL = 0x56
YWD_DATA = 0x57
YWD_RF = 0x58
YWD_RX = 0x59

CTRL_PING = 0x01
CTRL_GET_CAPS = 0x02
CTRL_GET_INFO = 0x03

RF_GET_STATUS = 0x01
RF_TX_TONES = 0x02
RF_ABORT = 0x03
RF_EXIT = 0x04
RF_GET_DIAG = 0x05

RX_START = 0x01
RX_READ = 0x02
RX_STOP = 0x03
RX_STATUS = 0x04
RX_RSSI = 0x05

RX_PROTOCOL_REVISION = 3
MAX_FRAME_BYTES = 255
MAX_RX_READ_BYTES = 200
MAX_SELECTORS = 1920


@dataclass(frozen=True)
class Frame:
    command: int
    payload: bytes

    @property
    def encoded(self) -> bytes:
        return build_frame(self.command, self.payload)


@dataclass(frozen=True)
class VersionResponse:
    protocol_version: int
    identity: str


@dataclass(frozen=True)
class NAKResponse:
    command: int
    error: int


@dataclass(frozen=True)
class RFStatus:
    flags: int
    remaining_selectors: int
    mode: int


@dataclass(frozen=True)
class RFDiagnostics:
    interrupt_count: int
    keyups: int
    generated_samples: int
    tx_active: int


@dataclass(frozen=True)
class RX3Status:
    flags: int
    available_bytes: int
    samples: int
    dropped_bytes: int


@dataclass(frozen=True)
class RXRSSI:
    """Raw ADF7021 RSSI magnitude returned by AX25R4 firmware.

    0C-P2 intentionally preserves the modem's uint16 value without assigning a
    carrier threshold or pretending it is already a calibrated DCD boolean.
    """

    raw_magnitude: int


def _octet(value: int, label: str) -> int:
    if not 0 <= value <= 0xFF:
        raise ValueError(f"{label} must fit in one byte")
    return value


def build_frame(command: int, payload: bytes = b"") -> bytes:
    """Build one complete MMDVM host frame.

    The length octet includes START, length, command and payload.
    """
    _octet(command, "command")
    total = 3 + len(payload)
    if total > MAX_FRAME_BYTES:
        raise ValueError("MMDVM host frame exceeds one-byte length field")
    return bytes((START, total, command)) + payload


def parse_frame(data: bytes, *, expected_command: int | None = None) -> Frame:
    if len(data) < 3:
        raise ValueError("truncated MMDVM host frame")
    if data[0] != START:
        raise ValueError("invalid MMDVM frame start byte")
    declared = data[1]
    if declared < 3:
        raise ValueError("invalid MMDVM frame length")
    if declared != len(data):
        raise ValueError(
            f"MMDVM frame length mismatch: declared={declared} actual={len(data)}"
        )
    command = data[2]
    if expected_command is not None and command != expected_command:
        raise ValueError(
            f"unexpected MMDVM response command 0x{command:02X}; "
            f"expected 0x{expected_command:02X}"
        )
    return Frame(command=command, payload=data[3:])


def ack_for(command: int) -> bytes:
    return build_frame(ACK, bytes((_octet(command, "command"),)))


def nak_for(command: int, error: int) -> bytes:
    return build_frame(
        NAK,
        bytes((_octet(command, "command"), _octet(error, "error"))),
    )


def parse_ack(data: bytes, *, expected_command: int) -> None:
    frame = parse_frame(data, expected_command=ACK)
    if frame.payload != bytes((_octet(expected_command, "expected command"),)):
        raise ValueError("ACK does not match the expected command")


def parse_nak(data: bytes) -> NAKResponse:
    frame = parse_frame(data, expected_command=NAK)
    if len(frame.payload) != 2:
        raise ValueError("malformed NAK response")
    return NAKResponse(command=frame.payload[0], error=frame.payload[1])


def get_version_request() -> bytes:
    return build_frame(GET_VERSION)


def parse_version_response(data: bytes) -> VersionResponse:
    frame = parse_frame(data, expected_command=GET_VERSION)
    if len(frame.payload) < 2:
        raise ValueError("GET_VERSION response is missing protocol/identity data")
    protocol_version = frame.payload[0]
    raw_identity = frame.payload[1:].split(b"\0", 1)[0]
    identity = raw_identity.decode("ascii", "strict").strip()
    if not identity:
        raise ValueError("GET_VERSION response identity is empty")
    return VersionResponse(protocol_version=protocol_version, identity=identity)


def control_request(subcommand: int) -> bytes:
    return build_frame(YWD_CONTROL, bytes((_octet(subcommand, "control subcommand"),)))


def rf_status_request() -> bytes:
    return build_frame(YWD_RF, bytes((RF_GET_STATUS,)))


def parse_rf_status(data: bytes) -> RFStatus:
    frame = parse_frame(data, expected_command=YWD_RF)
    if len(frame.payload) != 6 or frame.payload[:2] != bytes((RF_GET_STATUS, 1)):
        raise ValueError("malformed YWD_RF/GET_STATUS response")
    flags = frame.payload[2]
    remaining = frame.payload[3] | (frame.payload[4] << 8)
    mode = frame.payload[5]
    return RFStatus(flags=flags, remaining_selectors=remaining, mode=mode)


def rf_diag_request() -> bytes:
    return build_frame(YWD_RF, bytes((RF_GET_DIAG,)))


def parse_rf_diagnostics(data: bytes) -> RFDiagnostics:
    frame = parse_frame(data, expected_command=YWD_RF)
    if len(frame.payload) != 7 or frame.payload[0] != RF_GET_DIAG:
        raise ValueError("malformed YWD_RF/GET_DIAG response")
    interrupt_count = frame.payload[1] | (frame.payload[2] << 8)
    keyups = frame.payload[3]
    generated_samples = frame.payload[4] | (frame.payload[5] << 8)
    tx_active = frame.payload[6]
    return RFDiagnostics(
        interrupt_count=interrupt_count,
        keyups=keyups,
        generated_samples=generated_samples,
        tx_active=tx_active,
    )


def rf_exit_request() -> bytes:
    return build_frame(YWD_RF, bytes((RF_EXIT,)))


def rf_abort_request() -> bytes:
    return build_frame(YWD_RF, bytes((RF_ABORT,)))


def rf_tx_tones_request(*, selector_count: int, packed_selectors: bytes) -> bytes:
    """Build the qualified YWD_RF/TX_TONES request without performing I/O."""
    if not 1 <= selector_count <= MAX_SELECTORS:
        raise ValueError(f"selector_count must be 1..{MAX_SELECTORS}")
    expected_bytes = (selector_count + 7) // 8
    if len(packed_selectors) != expected_bytes:
        raise ValueError(
            f"packed selector length mismatch: expected={expected_bytes} "
            f"actual={len(packed_selectors)}"
        )
    payload = bytes(
        (
            RF_TX_TONES,
            selector_count & 0xFF,
            (selector_count >> 8) & 0xFF,
        )
    ) + packed_selectors
    return build_frame(YWD_RF, payload)


def rx_start_request() -> bytes:
    return build_frame(YWD_RX, bytes((RX_START,)))


def rx_stop_request() -> bytes:
    return build_frame(YWD_RX, bytes((RX_STOP,)))


def rx_status_request() -> bytes:
    return build_frame(YWD_RX, bytes((RX_STATUS,)))


def rx_rssi_request() -> bytes:
    """Request one read-only raw ADF7021 RSSI sample from AX25R4 firmware."""
    return build_frame(YWD_RX, bytes((RX_RSSI,)))


def parse_rx_rssi(data: bytes) -> RXRSSI:
    frame = parse_frame(data, expected_command=YWD_RX)
    if len(frame.payload) != 3 or frame.payload[0] != RX_RSSI:
        raise ValueError("malformed YWD_RX/RSSI response")
    raw = frame.payload[1] | (frame.payload[2] << 8)
    return RXRSSI(raw_magnitude=raw)


def parse_rx3_status(data: bytes) -> RX3Status:
    frame = parse_frame(data, expected_command=YWD_RX)
    if len(frame.payload) != 11 or frame.payload[:2] != bytes((RX_STATUS, RX_PROTOCOL_REVISION)):
        raise ValueError("malformed YWD_RX/STATUS revision-3 response")
    flags = frame.payload[2]
    available = frame.payload[3] | (frame.payload[4] << 8)
    samples = (
        frame.payload[5]
        | (frame.payload[6] << 8)
        | (frame.payload[7] << 16)
        | (frame.payload[8] << 24)
    )
    dropped = frame.payload[9] | (frame.payload[10] << 8)
    return RX3Status(
        flags=flags,
        available_bytes=available,
        samples=samples,
        dropped_bytes=dropped,
    )


def rx_read_request(maximum: int = MAX_RX_READ_BYTES) -> bytes:
    if not 1 <= maximum <= MAX_RX_READ_BYTES:
        raise ValueError(f"maximum RX read must be 1..{MAX_RX_READ_BYTES}")
    return build_frame(YWD_RX, bytes((RX_READ, maximum)))


def parse_rx_read(data: bytes) -> bytes:
    frame = parse_frame(data, expected_command=YWD_RX)
    if len(frame.payload) < 2 or frame.payload[0] != RX_READ:
        raise ValueError("malformed YWD_RX/READ response")
    count = frame.payload[1]
    payload = frame.payload[2:]
    if count != len(payload):
        raise ValueError(
            f"YWD_RX/READ byte-count mismatch: declared={count} actual={len(payload)}"
        )
    return payload
