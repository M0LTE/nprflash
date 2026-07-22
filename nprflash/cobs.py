"""Consistent Overhead Byte Stuffing (Cheshire & Baker).

Implemented here rather than pulled from PyPI so the wire layer is
self-contained and directly testable against the reference frames in
tests/fixtures/frames.json.

COBS removes zero bytes from a payload so that 0x00 can be used unambiguously
as a frame delimiter. Each block is preceded by a length code: the code is the
number of bytes to the next zero (counting itself), and a code of 0xFF marks a
maximal 254-byte run with no implied zero after it.
"""

MAX_BLOCK = 0xFE  # 254 data bytes is the longest run a single code can cover


def encode(data: bytes) -> bytes:
    """COBS-encode data. The result never contains a zero byte."""
    out = bytearray()
    block = bytearray()
    for byte in data:
        if byte == 0:
            out.append(len(block) + 1)
            out += block
            block.clear()
        else:
            block.append(byte)
            if len(block) == MAX_BLOCK:
                # A full run, with no zero to consume: emit it with the 0xFF
                # code so the decoder knows not to insert one.
                out.append(0xFF)
                out += block
                block.clear()
    out.append(len(block) + 1)
    out += block
    return bytes(out)


def decode(data: bytes) -> bytes:
    """Decode COBS data. `data` must NOT include the 0x00 frame delimiter.

    Encoded data cannot legitimately contain a zero anywhere -- that is the
    property the encoding exists to provide -- so any zero means the frame was
    mis-split or corrupted, and is rejected rather than silently decoded.
    """
    zero = data.find(0)
    if zero != -1:
        raise ValueError(
            f"zero byte inside COBS-encoded data at offset {zero}; "
            "the frame delimiter should have been stripped first")

    out = bytearray()
    i = 0
    end = len(data)
    while i < end:
        code = data[i]
        i += 1
        stop = i + code - 1
        if stop > end:
            raise ValueError(
                f"truncated COBS block: code {code} at offset {i - 1} "
                f"runs past the {end}-byte frame")
        out += data[i:stop]
        i = stop
        # A non-maximal block implies a zero, except at the very end.
        if code != 0xFF and i < end:
            out.append(0)
    return bytes(out)
