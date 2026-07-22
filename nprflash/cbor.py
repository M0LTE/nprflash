"""The minimal CBOR subset the `.nfw` container needs (RFC 8949).

Deliberately **not** a general CBOR implementation. The container is a map of
five fixed keys holding non-negative integers and byte strings, so that is all
this supports, and anything else is rejected rather than half-handled. Keeping
it in-tree means the only external dependency is pyserial, which distributions
package -- so the tool runs from a clone with nothing to install.

Supported: definite-length text strings, byte strings, non-negative integers,
and maps of those. Encoding always uses the shortest form for a value, which is
what the container's own encoder emits.

===========================  =========================================
major type                   used for
===========================  =========================================
0  unsigned integer          ``v``, ``ver``, ``hw``
2  byte string               ``crc``, ``data``
3  text string               the map keys
5  map                       the container itself
===========================  =========================================
"""

from __future__ import annotations

import struct

MAJOR_UINT = 0
MAJOR_BYTES = 2
MAJOR_TEXT = 3
MAJOR_MAP = 5


class CborError(ValueError):
    """Malformed, truncated, or unsupported CBOR."""


# -- encoding -------------------------------------------------------------

def _head(major: int, length: int) -> bytes:
    """Encode a type/length header using the shortest form for `length`."""
    prefix = major << 5
    if length < 24:
        return bytes([prefix | length])
    if length < 0x100:
        return bytes([prefix | 24, length])
    if length < 0x10000:
        return bytes([prefix | 25]) + struct.pack(">H", length)
    if length < 0x100000000:
        return bytes([prefix | 26]) + struct.pack(">I", length)
    if length < 0x10000000000000000:
        return bytes([prefix | 27]) + struct.pack(">Q", length)
    raise CborError(f"value too large to encode: {length}")


def _dump(value) -> bytes:
    if isinstance(value, bool):
        # bool is a subclass of int; refuse rather than silently encode 0/1.
        raise CborError("booleans are not supported")
    if isinstance(value, int):
        if value < 0:
            raise CborError(f"negative integers are not supported: {value}")
        return _head(MAJOR_UINT, value)
    if isinstance(value, bytes):
        return _head(MAJOR_BYTES, len(value)) + value
    if isinstance(value, str):
        encoded = value.encode("utf-8")
        return _head(MAJOR_TEXT, len(encoded)) + encoded
    if isinstance(value, dict):
        out = bytearray(_head(MAJOR_MAP, len(value)))
        for key, item in value.items():
            out += _dump(key)
            out += _dump(item)
        return bytes(out)
    raise CborError(f"unsupported type: {type(value).__name__}")


def dumps(value) -> bytes:
    """Encode a value. Map key order is preserved as given."""
    return _dump(value)


# -- decoding -------------------------------------------------------------

def _read_head(data: bytes, pos: int) -> tuple[int, int, int]:
    """Return (major, argument, next_pos)."""
    if pos >= len(data):
        raise CborError(f"truncated: expected a header at offset {pos}")
    initial = data[pos]
    major, extra = initial >> 5, initial & 0x1F
    pos += 1

    if extra < 24:
        return major, extra, pos
    if extra in (24, 25, 26, 27):
        width = 1 << (extra - 24)
        if pos + width > len(data):
            raise CborError(
                f"truncated: {width}-byte argument at offset {pos - 1} "
                f"runs past the end")
        fmt = {1: ">B", 2: ">H", 4: ">I", 8: ">Q"}[width]
        return major, struct.unpack(fmt, data[pos:pos + width])[0], pos + width
    if extra == 31:
        raise CborError("indefinite-length items are not supported")
    raise CborError(f"reserved additional-information value {extra}")


def _load(data: bytes, pos: int):
    major, arg, pos = _read_head(data, pos)

    if major == MAJOR_UINT:
        return arg, pos
    if major in (MAJOR_BYTES, MAJOR_TEXT):
        end = pos + arg
        if end > len(data):
            raise CborError(
                f"truncated: string of {arg} bytes at offset {pos} "
                f"runs past the end")
        chunk = data[pos:end]
        return (chunk if major == MAJOR_BYTES else chunk.decode("utf-8")), end
    if major == MAJOR_MAP:
        out = {}
        for _ in range(arg):
            key, pos = _load(data, pos)
            value, pos = _load(data, pos)
            out[key] = value
        return out, pos
    raise CborError(f"unsupported major type {major}")


def loads(data: bytes):
    """Decode a value. Trailing bytes are an error."""
    value, pos = _load(data, 0)
    if pos != len(data):
        raise CborError(f"{len(data) - pos} trailing bytes after the value")
    return value
