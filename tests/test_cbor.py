"""The CBOR subset the container needs.

Only what the format uses is supported: definite-length non-negative integers,
byte strings, text strings and maps. Everything else must be refused rather
than half-handled.
"""

import pytest

from nprflash import cbor
from nprflash.cbor import CborError


@pytest.mark.parametrize("value", [
    0, 1, 23, 24, 25, 255, 256, 65535, 65536,
    2**32 - 1, 2**32, 2**64 - 1, 25121101, 26072208,
])
def test_integers_round_trip_across_every_length_boundary(value):
    assert cbor.loads(cbor.dumps(value)) == value


@pytest.mark.parametrize("value, expected", [
    (0, "00"), (23, "17"), (24, "1818"), (255, "18ff"),
    (256, "190100"), (65535, "19ffff"), (65536, "1a00010000"),
    (2**32, "1b0000000100000000"),
])
def test_integers_use_the_shortest_form(value, expected):
    """RFC 8949 shortest-form encoding, as the container's own encoder emits."""
    assert cbor.dumps(value).hex() == expected


@pytest.mark.parametrize("size", [0, 1, 23, 24, 255, 256, 65535, 65536, 111616])
def test_byte_strings_round_trip(size):
    value = bytes(size)
    assert cbor.loads(cbor.dumps(value)) == value


def test_byte_string_headers():
    assert cbor.dumps(b"").hex() == "40"
    assert cbor.dumps(b"\x01" * 23).hex().startswith("57")
    assert cbor.dumps(b"\x01" * 24).hex().startswith("5818")
    assert cbor.dumps(b"\x01" * 256).hex().startswith("590100")


def test_text_strings_round_trip():
    for value in ("", "v", "crc", "data", "hardware"):
        assert cbor.loads(cbor.dumps(value)) == value


def test_container_shaped_map_round_trips_and_preserves_key_order():
    value = {"v": 1, "crc": b"\x2a\xa1\x52\xf6", "ver": 25121101,
             "hw": 240719, "data": b"\xa5" * 2048}
    encoded = cbor.dumps(value)
    assert encoded[0] == 0xA5  # map, five pairs
    decoded = cbor.loads(encoded)
    assert decoded == value
    assert list(decoded) == list(value)


@pytest.mark.parametrize("value", [-1, -256, True, False, 1.5, None, [1, 2], {1: 2.0}])
def test_unsupported_values_are_refused(value):
    with pytest.raises(CborError):
        cbor.dumps(value)


@pytest.mark.parametrize("blob, match", [
    (b"", "truncated"),
    (b"\x18", "truncated"),
    (b"\x42\x01", "truncated"),
    (b"\xa1\x61a", "truncated"),
    (b"\x9f", "indefinite"),
    (b"\x5f", "indefinite"),
    (b"\x1c", "reserved"),
    (b"\x01\x02", "trailing"),
    (b"\xc0\x01", "unsupported major type"),
])
def test_malformed_input_is_rejected(blob, match):
    with pytest.raises(CborError, match=match):
        cbor.loads(blob)


def test_decoding_is_not_fooled_by_a_length_that_overruns():
    with pytest.raises(CborError, match="truncated"):
        cbor.loads(b"\x5a\x00\x01\x00\x00" + b"\x00" * 10)
