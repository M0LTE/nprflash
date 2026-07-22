"""The flash sequence, driven against a fake transport.

No hardware needed: a stub replays the exact replies the real device gave,
so the ordering and error handling are exercised deterministically.
"""

import pytest

from nprflash import protocol
from nprflash.bootloader import Bootloader, HardwareMismatch
from nprflash.container import Container
from nprflash.protocol import CommandFailed, Opcode, Status

HW = 240719
BL_VERSION = 24120501


class FakeTransport:
    """Records outgoing frames and replies with canned status bytes."""

    def __init__(self, statuses=None):
        self.sent = []
        self.statuses = list(statuses or [])

    def send_frame(self, frame):
        self.sent.append(protocol.decode_frame(frame))

    def recv_frame(self):
        opcode = self.sent[-1][0]
        if opcode == Opcode.BL_INFO:
            import struct
            return protocol.encode_frame(
                bytes([Opcode.BL_INFO]) + struct.pack("<II", HW, BL_VERSION))[:-1]
        status = self.statuses.pop(0) if self.statuses else Status.OK
        return protocol.encode_frame(bytes([0, int(status)]))[:-1]

    @property
    def opcodes(self):
        return [f[0] for f in self.sent]


@pytest.fixture
def firmware():
    return Container.build(b"\xa5" * 3000, version=26072206, hardware_id=HW)


def test_identify(firmware):
    info = Bootloader(FakeTransport()).identify()
    assert info.hardware_id == HW
    assert info.bootloader_version == BL_VERSION


def test_flash_sends_the_documented_sequence(firmware):
    t = FakeTransport()
    Bootloader(t).flash(firmware)
    # BL_INFO, FW_INFO, one FW_DATA per block, FW_FINALIZE
    assert t.opcodes == ([Opcode.BL_INFO, Opcode.FW_INFO]
                         + [Opcode.FW_DATA] * firmware.block_count
                         + [Opcode.FW_FINALIZE])


def test_flash_covers_the_payload_exactly_once(firmware):
    import struct
    t = FakeTransport()
    Bootloader(t).flash(firmware)
    blocks = [f for f in t.sent if f[0] == Opcode.FW_DATA]
    offsets, rebuilt = [], b""
    for f in blocks:
        _, _, offset = struct.unpack("<III", f[1:13])
        offsets.append(offset)
        rebuilt += f[13:]
    assert offsets == [i * 1024 for i in range(firmware.block_count)]
    assert rebuilt == firmware.image


def test_progress_reports_reach_the_total(firmware):
    seen = []
    Bootloader(FakeTransport()).flash(firmware, progress=lambda d, t: seen.append((d, t)))
    assert seen[0] == (0, len(firmware.image))
    assert seen[-1] == (len(firmware.image), len(firmware.image))
    assert [d for d, _ in seen] == sorted(d for d, _ in seen)


def test_hardware_mismatch_is_caught_before_any_write():
    other = Container.build(b"\x00" * 1024, version=1, hardware_id=111111)
    t = FakeTransport()
    with pytest.raises(HardwareMismatch):
        Bootloader(t).flash(other)
    assert t.opcodes == [Opcode.BL_INFO]  # nothing written


def test_force_bypasses_the_hardware_check():
    other = Container.build(b"\x00" * 1024, version=1, hardware_id=111111)
    t = FakeTransport()
    Bootloader(t).flash(other, check_hardware=False)
    assert Opcode.FW_DATA in t.opcodes


def test_nack_at_fw_info_aborts_before_writing_data(firmware):
    t = FakeTransport(statuses=[Status.NACK])
    with pytest.raises(CommandFailed) as exc:
        Bootloader(t).flash(firmware)
    assert exc.value.status is Status.NACK
    assert Opcode.FW_DATA not in t.opcodes


def test_crc_fail_at_finalize_is_reported(firmware):
    n = firmware.block_count
    t = FakeTransport(statuses=[Status.OK] * (n + 1) + [Status.CRC_FAIL])
    with pytest.raises(CommandFailed) as exc:
        Bootloader(t).flash(firmware)
    assert exc.value.status is Status.CRC_FAIL
    assert exc.value.command is Opcode.FW_FINALIZE


def test_failure_mid_transfer_stops_immediately(firmware):
    t = FakeTransport(statuses=[Status.OK, Status.OK, Status.FAIL])
    with pytest.raises(CommandFailed):
        Bootloader(t).flash(firmware)
    assert t.opcodes.count(Opcode.FW_DATA) == 2
