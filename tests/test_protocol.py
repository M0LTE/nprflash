"""Requests must match the frames the device accepts, and replies must decode
to what it sends.

The reference vectors in tests/fixtures/frames.json are the ground truth for
wire compatibility: a change that breaks them breaks compatibility with the
device.
"""

import struct

import pytest

from nprflash import protocol
from nprflash.protocol import CommandFailed, Opcode, ProtocolError, Status


def wire(vectors, name):
    return bytes.fromhex(vectors[name]["wire_hex"])


def payload(vectors, name):
    return bytes.fromhex(vectors[name]["payload_hex"])


# -- requests reproduce the reference frames -------------------------------

def test_bl_info_request(vectors):
    assert protocol.encode_frame(protocol.bl_info()) == wire(vectors, "bl_info_request")


@pytest.mark.parametrize("name, hw, ver, length, crc", [
    ("fw_info_26072206", 240719, 26072206, 92160, "dc030b30"),
    ("fw_info_26072207", 240719, 26072207, 117760, "93035502"),
    ("fw_info_bad_hwid", 111111, 26072207, 117760, "93035502"),
    ("fw_info_bad_crc", 240719, 26072207, 117760, "6c035502"),
])
def test_fw_info_requests(vectors, name, hw, ver, length, crc):
    built = protocol.fw_info(hw, ver, length, bytes.fromhex(crc))
    assert protocol.encode_frame(built) == wire(vectors, name)


@pytest.mark.parametrize("name, hw, ver", [
    ("fw_finalize_26072206", 240719, 26072206),
    ("fw_finalize_26072207", 240719, 26072207),
])
def test_fw_finalize_requests(vectors, name, hw, ver):
    assert protocol.encode_frame(protocol.fw_finalize(hw, ver)) == wire(vectors, name)


# -- FW_DATA framing -------------------------------------------------------

@pytest.mark.parametrize("filler", [
    b"\x00", b"\xff", b"\xa5",                       # uniform
    bytes(range(256)),                               # every byte value
])
def test_fw_data_layout(filler):
    """Header is <III hw, ver, offset; the block follows verbatim."""
    chunk = (filler * (protocol.BLOCK_SIZE // len(filler) + 1))[:protocol.BLOCK_SIZE]
    frame = protocol.fw_data(240719, 26072208, 4096, chunk)
    assert frame[0] == Opcode.FW_DATA
    assert struct.unpack("<III", frame[1:13]) == (240719, 26072208, 4096)
    assert frame[13:] == chunk
    # And it survives framing intact, whatever the block contains.
    assert protocol.decode_frame(protocol.encode_frame(frame)) == frame


def test_fw_data_frames_are_delimiter_safe():
    """A block full of zeros must still produce a frame with exactly one 0x00."""
    frame = protocol.encode_frame(
        protocol.fw_data(240719, 26072208, 0, bytes(protocol.BLOCK_SIZE)))
    assert frame.count(0) == 1 and frame.endswith(b"\x00")


def test_fw_info_rejects_bad_crc_length():
    with pytest.raises(ValueError, match="4 bytes"):
        protocol.fw_info(1, 2, 3, b"\x00")


# -- replies decode correctly ----------------------------------------------

def test_bl_info_reply(vectors):
    assert protocol.parse_bl_info(payload(vectors, "bl_info_response")) == (240719, 24120501)


def test_ok_status(vectors):
    assert protocol.parse_status(payload(vectors, "response_ok"),
                                 Opcode.FW_INFO) is Status.OK


@pytest.mark.parametrize("name, expected", [
    ("response_nack", Status.NACK),
    ("response_crc_fail", Status.CRC_FAIL),
])
def test_failure_statuses_raise(vectors, name, expected):
    with pytest.raises(CommandFailed) as exc:
        protocol.parse_status(payload(vectors, name), Opcode.FW_INFO)
    assert exc.value.status is expected
    assert expected.name in str(exc.value)


# -- malformed replies are diagnosed, not misread --------------------------

def test_empty_reply_is_an_error():
    with pytest.raises(ProtocolError, match="no reply"):
        protocol.parse_status(b"", Opcode.FW_INFO)


def test_short_reply_is_an_error():
    with pytest.raises(ProtocolError, match="too short"):
        protocol.parse_status(b"\x00", Opcode.FW_INFO)


def test_unexpected_leading_byte_is_an_error():
    with pytest.raises(ProtocolError, match="leading byte"):
        protocol.parse_status(b"\x07\x00", Opcode.FW_INFO)


def test_unknown_status_code_is_an_error():
    with pytest.raises(ProtocolError, match="unknown status"):
        protocol.parse_status(b"\x00\x7f", Opcode.FW_INFO)


def test_bl_info_wrong_opcode_is_an_error():
    with pytest.raises(ProtocolError, match="opcode"):
        protocol.parse_bl_info(b"\x02" + b"\x00" * 8)


def test_bl_info_short_reply_is_an_error():
    with pytest.raises(ProtocolError, match="too short"):
        protocol.parse_bl_info(b"\x07\x00")


def test_status_values_are_stable():
    assert [s.value for s in Status] == [0, 1, 2, 3, 4, 5, 6]
    assert Status.CRC_FAIL == 3 and Status.NACK == 4


def test_opcode_values_are_stable():
    assert (Opcode.FW_INFO, Opcode.FW_DATA,
            Opcode.FW_FINALIZE, Opcode.BL_INFO) == (2, 4, 6, 7)
