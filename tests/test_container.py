"""The .nfw container: padding, checksum, and round-tripping."""

import binascii
import struct

import pytest

from nprflash.container import BLOCK_SIZE, Container, ContainerError, checksum


def test_round_trips():
    fw = Container.build(b"\xa5" * 3000, version=26072208, hardware_id=240719)
    again = Container.parse(fw.to_bytes())
    assert (again.version, again.hardware_id) == (26072208, 240719)
    assert again.image == fw.image
    assert again.crc == fw.crc
    assert again.crc_is_valid()


@pytest.mark.parametrize("size", [0, 1, 1023, 1024, 1025, 111616])
def test_payload_is_padded_to_whole_blocks(size):
    fw = Container.build(b"\xa5" * size, version=1, hardware_id=2)
    assert len(fw.image) % BLOCK_SIZE == 0
    assert len(fw.image) - size < BLOCK_SIZE
    assert fw.image[:size] == b"\xa5" * size
    assert set(fw.image[size:]) <= {0}
    assert fw.block_count == len(fw.image) // BLOCK_SIZE


@pytest.mark.parametrize("payload, expected", [
    (b"\xa5" * 1024, "7cda55fe"),
    (b"\x00" * 1024, "2eafb5ef"),
    (bytes(range(256)) * 4, "264c0bb7"),
])
def test_checksum_is_crc32_stored_little_endian(payload, expected):
    assert checksum(payload).hex() == expected
    assert checksum(payload) == struct.pack("<I", binascii.crc32(payload))


def test_checksum_covers_the_padding_not_just_the_input():
    """The device CRCs what it receives, which includes the pad bytes."""
    fw = Container.build(b"\xa5" * 10, version=1, hardware_id=2)
    assert fw.crc == checksum(fw.image)
    assert fw.crc != checksum(b"\xa5" * 10)


def test_detects_corrupt_crc():
    fw = Container.build(b"\xa5" * 1024, version=1, hardware_id=2)
    bad = Container(version=fw.version, hardware_id=fw.hardware_id,
                   image=fw.image, crc=b"\xde\xad\xbe\xef")
    assert fw.crc_is_valid()
    assert not bad.crc_is_valid()


def test_describe_reports_the_key_fields():
    text = Container.build(b"\x00" * 2048, version=26072208,
                          hardware_id=240719).describe()
    assert "26072208" in text and "240719" in text and "2048" in text


@pytest.mark.parametrize("blob, match", [
    (b"", "bad header"),
    (b"XXXX\xa0", "bad header"),
    (b"NOVA\xff\xff", "CBOR"),
])
def test_rejects_malformed_containers(blob, match):
    with pytest.raises(ContainerError, match=match):
        Container.parse(blob)


def test_rejects_unknown_container_version():
    from nprflash import cbor as cbor2
    blob = b"NOVA" + cbor2.dumps({"v": 99, "crc": b"\x00" * 4, "ver": 1,
                                  "hw": 2, "data": b""})
    with pytest.raises(ContainerError, match="unsupported container version"):
        Container.parse(blob)


def test_rejects_missing_key():
    from nprflash import cbor as cbor2
    blob = b"NOVA" + cbor2.dumps({"v": 1, "ver": 1, "hw": 2, "data": b""})
    with pytest.raises(ContainerError, match="missing key"):
        Container.parse(blob)


def test_rejects_unpadded_payload():
    with pytest.raises(ContainerError, match="multiple of 1024"):
        Container(version=1, hardware_id=2, image=b"\x00" * 100, crc=b"\x00" * 4)


def test_rejects_wrong_length_crc():
    with pytest.raises(ContainerError, match="crc must be 4 bytes"):
        Container(version=1, hardware_id=2, image=b"", crc=b"\x00")
