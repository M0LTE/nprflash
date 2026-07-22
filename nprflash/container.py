"""The NOVA (`.nfw`) firmware container.

The 4-byte magic ``NOVA`` followed by a CBOR map.

===========  ===========  ==================================================
key          type         meaning
===========  ===========  ==================================================
``v``        int          container version; only ``1`` is known
``crc``      bytes(4)     CRC-32 of the padded payload, little-endian
``ver``      int          firmware version, YYMMDDRR
``hw``       int          hardware ID the image is built for
``data``     bytes        payload, zero-padded to a 1024-byte multiple
===========  ===========  ==================================================

There is no encryption -- only zero-padding to a whole number of blocks and a
CRC-32, stored little-endian.
"""

from __future__ import annotations

import binascii
import struct
from dataclasses import dataclass

MAGIC = b"NOVA"
CONTAINER_VERSION = 1

#: The bootloader consumes firmware in fixed-size blocks; payloads are padded
#: up to a whole number of them.
BLOCK_SIZE = 1024


class ContainerError(ValueError):
    """A `.nfw` container is malformed or unsupported."""


def pad_payload(raw: bytes) -> bytes:
    """Zero-pad to a whole number of BLOCK_SIZE blocks."""
    return raw + b"\x00" * (-len(raw) % BLOCK_SIZE)


def checksum(padded: bytes) -> bytes:
    """CRC-32 of the padded payload, packed the way the container stores it."""
    return struct.pack("<I", binascii.crc32(padded) & 0xFFFFFFFF)


@dataclass(frozen=True)
class Firmware:
    """A parsed or freshly-built firmware container."""

    version: int
    hardware_id: int
    payload: bytes
    crc: bytes

    def __post_init__(self) -> None:
        if len(self.crc) != 4:
            raise ContainerError(f"crc must be 4 bytes, got {len(self.crc)}")
        if len(self.payload) % BLOCK_SIZE:
            raise ContainerError(
                f"payload must be a multiple of {BLOCK_SIZE} bytes, "
                f"got {len(self.payload)}")

    # -- construction ----------------------------------------------------

    @classmethod
    def build(cls, raw: bytes, *, version: int, hardware_id: int) -> "Firmware":
        """Wrap a raw firmware binary, padding it and computing its CRC."""
        padded = pad_payload(raw)
        return cls(version=version, hardware_id=hardware_id,
                   payload=padded, crc=checksum(padded))

    @classmethod
    def parse(cls, blob: bytes) -> "Firmware":
        """Parse a `.nfw` container."""
        import cbor2  # deferred: parsing is optional for flash-only use

        if len(blob) < len(MAGIC) or not blob.startswith(MAGIC):
            raise ContainerError("bad header: expected NOVA magic")
        try:
            fields = cbor2.loads(blob[len(MAGIC):])
        except Exception as ex:  # cbor2 raises several distinct types
            raise ContainerError(f"failed to decode CBOR body: {ex}") from ex
        if not isinstance(fields, dict):
            raise ContainerError(
                f"expected a CBOR map, got {type(fields).__name__}")

        got = fields.get("v")
        if got != CONTAINER_VERSION:
            raise ContainerError(f"unsupported container version {got!r}")

        for key, want in (("crc", bytes), ("ver", int),
                          ("hw", int), ("data", bytes)):
            if key not in fields:
                raise ContainerError(f"missing key {key!r}")
            if not isinstance(fields[key], want):
                raise ContainerError(
                    f"key {key!r} should be {want.__name__}, "
                    f"got {type(fields[key]).__name__}")

        return cls(version=fields["ver"], hardware_id=fields["hw"],
                   payload=fields["data"], crc=fields["crc"])

    # -- serialisation ---------------------------------------------------

    def to_bytes(self) -> bytes:
        """Serialise back to a `.nfw` container."""
        import cbor2

        return MAGIC + cbor2.dumps({
            "v": CONTAINER_VERSION,
            "crc": self.crc,
            "ver": self.version,
            "hw": self.hardware_id,
            "data": self.payload,
        })

    # -- inspection ------------------------------------------------------

    @property
    def block_count(self) -> int:
        return len(self.payload) // BLOCK_SIZE

    def crc_is_valid(self) -> bool:
        """Whether the stored CRC matches the payload.

        Worth checking before flashing: the device does not verify the CRC
        until FW_FINALIZE, by which point it has already written everything.
        """
        return self.crc == checksum(self.payload)

    def describe(self) -> str:
        return (f"Firmware version:   {self.version}\n"
                f"Hardware ID:        {self.hardware_id}\n"
                f"CRC:                {self.crc.hex()}\n"
                f"Payload size:       {len(self.payload)} bytes "
                f"({self.block_count} blocks)")
