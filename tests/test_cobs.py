"""COBS must match the encoding the device speaks, exactly."""

import pytest

from nprflash import cobs


@pytest.mark.parametrize("raw", [
    b"", b"\x00", b"\x01", b"\x00\x00", b"\x01\x00\x02", b"\x11\x22\x00\x33",
    # 254 is the block boundary: a run of exactly that length must be emitted
    # with the 0xFF code and NOT gain an implied zero.
    bytes(range(1, 255)), b"\xaa" * 253, b"\xaa" * 254, b"\xaa" * 255,
    b"\xaa" * 254 + b"\x00", b"\x00" + b"\xaa" * 254,
    # Long zero runs, the other direction the encoding has to handle.
    bytes(254), bytes(255), bytes(1024),
    # Mixed, and larger than one FW_DATA block.
    bytes(range(256)) * 4, (b"\x00" * 300) + (b"\xff" * 300),
    bytes(range(256)) * 8,
])
def test_round_trip(raw):
    assert cobs.decode(cobs.encode(raw)) == raw


@pytest.mark.parametrize("size", [0, 1, 253, 254, 255, 1024, 2048])
def test_encoded_output_never_contains_zero(size):
    for raw in (bytes(size), b"\xff" * size, bytes(range(256)) * (size // 256 + 1)):
        assert 0 not in cobs.encode(raw)


def test_maximal_run_uses_the_ff_code():
    """254 non-zero bytes is the longest a single code can cover."""
    encoded = cobs.encode(b"\xaa" * 254)
    assert encoded[0] == 0xFF
    assert len(encoded) == 256  # 0xFF + 254 data + trailing 0x01 block


def test_overhead_is_one_byte_per_254():
    for size in (1, 100, 254, 255, 1000):
        raw = b"\xaa" * size
        assert len(cobs.encode(raw)) == size + 1 + (size // 254)


def test_matches_reference_frames(vectors):
    """The frames the device actually exchanges."""
    for name, vec in vectors.items():
        wire = bytes.fromhex(vec["wire_hex"])
        payload = bytes.fromhex(vec["payload_hex"])
        assert cobs.encode(payload) + b"\x00" == wire, f"encode mismatch: {name}"
        assert cobs.decode(wire[:-1]) == payload, f"decode mismatch: {name}"


def test_rejects_zero_inside_frame():
    with pytest.raises(ValueError, match="zero byte"):
        cobs.decode(b"\x03\x11\x00")


def test_rejects_truncated_block():
    with pytest.raises(ValueError, match="truncated"):
        cobs.decode(b"\x05\x11\x22")
