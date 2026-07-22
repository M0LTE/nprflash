"""Wire protocol spoken by the NPR-H 3.0 bootloader.

Frames are COBS-encoded and terminated by a single ``0x00`` byte. The first
byte of a decoded payload is the opcode; multi-byte fields are little-endian.

=========  =============  ===============================================
opcode     command        payload after the opcode
=========  =============  ===============================================
``7``      BL_INFO        (none)
``2``      FW_INFO        ``<III`` hw, version, total_len, then 4-byte CRC
``4``      FW_DATA        ``<III`` hw, version, offset, then 1024 bytes
``6``      FW_FINALIZE    ``<II`` hw, version
=========  =============  ===============================================

Replies come in two shapes, both observed on hardware:

* **BL_INFO** echoes opcode ``7`` and appends ``<II`` hardware ID and
  bootloader version.
* **Everything else** replies with two bytes: a leading ``0x00`` and a
  :class:`Status` code.

Validation order matters and is not obvious. The device checks the **hardware
ID at FW_INFO**, before accepting any data -- a wrong-hardware image is
rejected without touching the installed firmware. It checks the **CRC only at
FW_FINALIZE**, after every block has been written, so a corrupt image *is*
written to the application partition before being rejected.

Every constant here is pinned by reference frames in
tests/fixtures/frames.json.
"""

from __future__ import annotations

import struct
from enum import IntEnum

from . import cobs

DELIMITER = b"\x00"

#: Firmware is transferred in fixed-size blocks.
BLOCK_SIZE = 1024


class Opcode(IntEnum):
    FW_INFO = 2
    FW_DATA = 4
    FW_FINALIZE = 6
    BL_INFO = 7


class Status(IntEnum):
    """Status byte returned for FW_* commands."""

    OK = 0
    FAIL = 1
    ABORTED = 2
    CRC_FAIL = 3
    NACK = 4
    NOT_AVAIL = 5
    INVALID = 6

    def explain(self) -> str:
        return {
            Status.OK: "Success.",
            Status.FAIL: "Generic failure.",
            Status.ABORTED: "The operation was aborted by the device.",
            Status.CRC_FAIL: "CRC check failed -- the payload the device "
                             "received does not match the declared checksum.",
            Status.NACK: "Not accepted: the firmware version or hardware ID "
                         "was invalid for this hardware or bootloader state.",
            Status.NOT_AVAIL: "The requested function is not available.",
            Status.INVALID: "The command or its parameters were invalid.",
        }[self]


class ProtocolError(Exception):
    """A reply was absent, malformed, or reported failure."""


class CommandFailed(ProtocolError):
    """The device returned a non-OK status."""

    def __init__(self, command: Opcode, status: Status):
        self.command = command
        self.status = status
        super().__init__(f"{command.name} failed: {status.name}: {status.explain()}")


# -- framing --------------------------------------------------------------

def encode_frame(payload: bytes) -> bytes:
    """COBS-encode a payload and append the frame delimiter."""
    return cobs.encode(payload) + DELIMITER


def decode_frame(frame: bytes) -> bytes:
    """Decode a frame, with or without its trailing delimiter."""
    if frame.endswith(DELIMITER):
        frame = frame[:-1]
    return cobs.decode(frame)


# -- requests -------------------------------------------------------------

def bl_info() -> bytes:
    return bytes([Opcode.BL_INFO])


def fw_info(hardware_id: int, version: int, total_len: int, crc: bytes) -> bytes:
    if len(crc) != 4:
        raise ValueError(f"crc must be 4 bytes, got {len(crc)}")
    return (bytes([Opcode.FW_INFO])
            + struct.pack("<III", hardware_id, version, total_len) + crc)


def fw_data(hardware_id: int, version: int, offset: int, chunk: bytes) -> bytes:
    # The offset is relative to the start of the payload, NOT an absolute flash
    # address -- the bootloader supplies the 0x08020000 base itself. This is
    # why the protocol cannot be made to overwrite the bootloader.
    return (bytes([Opcode.FW_DATA])
            + struct.pack("<III", hardware_id, version, offset) + chunk)


def fw_finalize(hardware_id: int, version: int) -> bytes:
    return bytes([Opcode.FW_FINALIZE]) + struct.pack("<II", hardware_id, version)


# -- replies --------------------------------------------------------------

def parse_bl_info(payload: bytes) -> tuple[int, int]:
    """Return (hardware_id, bootloader_version) from a BL_INFO reply."""
    if not payload:
        raise ProtocolError("empty reply to BL_INFO")
    if payload[0] != Opcode.BL_INFO:
        raise ProtocolError(
            f"BL_INFO reply had opcode {payload[0]}, expected {int(Opcode.BL_INFO)}")
    if len(payload) < 9:
        raise ProtocolError(
            f"BL_INFO reply too short: {len(payload)} bytes, expected at least 9")
    return struct.unpack("<II", payload[1:9])


def parse_status(payload: bytes, command: Opcode) -> Status:
    """Validate a FW_* reply, raising CommandFailed unless it is OK."""
    if not payload:
        raise ProtocolError(f"no reply to {command.name} (timeout?)")
    if len(payload) < 2:
        raise ProtocolError(
            f"{command.name} reply too short: {payload.hex()}")
    if payload[0] != 0:
        raise ProtocolError(
            f"{command.name} reply had unexpected leading byte "
            f"0x{payload[0]:02x}: {payload.hex()}")
    try:
        status = Status(payload[1])
    except ValueError:
        raise ProtocolError(
            f"{command.name} returned unknown status 0x{payload[1]:02x}") from None
    if status is not Status.OK:
        raise CommandFailed(command, status)
    return status
