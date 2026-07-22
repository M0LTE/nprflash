"""The `.nfw` container.

Two distinct things are easy to conflate, and the difference matters:

**firmware image**
    The raw binary that executes on the MCU. This is what a build produces
    (`npr.bin`) and what ends up in the application partition.

**container**
    The `.nfw` wrapper around an image: the 4-byte magic ``NOVA`` followed by a
    CBOR map carrying the image plus the metadata the bootloader checks.

===========  ===========  ==================================================
key          type         meaning
===========  ===========  ==================================================
``v``        int          container format version; only ``1`` is known
``crc``      bytes(4)     CRC-32 of the padded image, little-endian
``ver``      int          firmware version, YYMMDDRR
``hw``       int          hardware ID the image is built for
``data``     bytes        the image, zero-padded to a 1024-byte multiple
===========  ===========  ==================================================

There is no encryption -- only zero-padding to a whole number of blocks and a
CRC-32.

Note that the two version numbers are set independently: the container's ``ver``
is what the bootloader validates, while the version the running firmware reports
is compiled into the image. Keeping them in step is the caller's job.

The padding is part of what is transmitted and written, so the checksum covers
it too.
"""

from __future__ import annotations

import binascii
import struct
from dataclasses import dataclass

from . import cbor

MAGIC = b"NOVA"
CONTAINER_VERSION = 1

#: The bootloader consumes firmware in fixed-size blocks; images are padded up
#: to a whole number of them.
BLOCK_SIZE = 1024


class ContainerError(ValueError):
    """A `.nfw` container is malformed or unsupported."""


def pad_image(image: bytes) -> bytes:
    """Zero-pad a firmware image to a whole number of BLOCK_SIZE blocks."""
    return image + b"\x00" * (-len(image) % BLOCK_SIZE)


def checksum(padded_image: bytes) -> bytes:
    """CRC-32 of the padded image, packed the way the container stores it."""
    return struct.pack("<I", binascii.crc32(padded_image) & 0xFFFFFFFF)


@dataclass(frozen=True)
class Container:
    """A parsed or freshly-built `.nfw` container."""

    version: int
    hardware_id: int
    image: bytes
    crc: bytes

    def __post_init__(self) -> None:
        if len(self.crc) != 4:
            raise ContainerError(f"crc must be 4 bytes, got {len(self.crc)}")
        if len(self.image) % BLOCK_SIZE:
            raise ContainerError(
                f"image must be padded to a multiple of {BLOCK_SIZE} bytes, "
                f"got {len(self.image)}")

    # -- construction ----------------------------------------------------

    @classmethod
    def build(cls, image: bytes, *, version: int, hardware_id: int) -> "Container":
        """Wrap a firmware image, padding it and computing its checksum."""
        padded = pad_image(image)
        return cls(version=version, hardware_id=hardware_id,
                   image=padded, crc=checksum(padded))

    @classmethod
    def parse(cls, blob: bytes) -> "Container":
        """Parse a `.nfw` container."""
        if len(blob) < len(MAGIC) or not blob.startswith(MAGIC):
            raise ContainerError("bad header: expected NOVA magic")
        try:
            fields = cbor.loads(blob[len(MAGIC):])
        except cbor.CborError as ex:
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
                   image=fields["data"], crc=fields["crc"])

    # -- serialisation ---------------------------------------------------

    def to_bytes(self) -> bytes:
        """Serialise back to a `.nfw` container."""
        return MAGIC + cbor.dumps({
            "v": CONTAINER_VERSION,
            "crc": self.crc,
            "ver": self.version,
            "hw": self.hardware_id,
            "data": self.image,
        })

    # -- inspection ------------------------------------------------------

    @property
    def block_count(self) -> int:
        return len(self.image) // BLOCK_SIZE

    def crc_is_valid(self) -> bool:
        """Whether the stored checksum matches the image.

        Worth checking before flashing: the device does not verify the checksum
        until the final commit, by which point it has written everything.
        """
        return self.crc == checksum(self.image)

    def describe(self) -> str:
        return (f"Firmware version:   {self.version}\n"
                f"Hardware ID:        {self.hardware_id}\n"
                f"CRC:                {self.crc.hex()}\n"
                f"Image size:         {len(self.image)} bytes "
                f"({self.block_count} blocks)")
