"""Ethernet install: framing, slot parsing and the upload sequence."""

from __future__ import annotations

import pytest

from nprflash.container import Container
from nprflash.hmi import PROMPT, strip_iac
from nprflash.netflash import (NetflashError, install, parse_slots, supported)


# -- telnet framing -------------------------------------------------------

def test_strip_iac_removes_option_negotiation():
    # WILL ECHO, DO SUPPRESS-GA, WILL SUPPRESS-GA, then text
    raw = bytes([0xFF, 0xFB, 0x01, 0xFF, 0xFD, 0x03, 0xFF, 0xFB, 0x03]) + b"NPR modem"
    clean, pending = strip_iac(raw)
    assert clean == b"NPR modem"
    assert pending == b""


def test_strip_iac_unescapes_literal_ff():
    clean, pending = strip_iac(bytes([0xFF, 0xFF, 0x41]))
    assert clean == bytes([0xFF, 0x41])
    assert pending == b""


@pytest.mark.parametrize("split", [1, 2])
def test_strip_iac_tolerates_sequence_split_across_reads(split):
    raw = bytes([0xFF, 0xFB, 0x01]) + b"hi"
    first, pending = strip_iac(raw[:split])
    second, tail = strip_iac(raw[split:], pending)
    assert first + second == b"hi"
    assert tail == b""


# -- slot table -----------------------------------------------------------

SLOTS_REPLY = """slots
descriptor valid (magic 58D94F01)
  slot0 @08020000 ver 26072302 size 119808
        crc C8BEB0D5 flags 00000101  <- boot
  slot1 @08060000 ver 26072301 size 115712
        crc FD197554 flags 00000001
ready> """


def test_parse_slots_reads_both_entries():
    slots = parse_slots(SLOTS_REPLY)
    assert [s["slot"] for s in slots] == [0, 1]
    assert slots[0]["address"] == 0x08020000
    assert slots[0]["version"] == 26072302
    assert slots[1]["size"] == 115712


def test_parse_slots_returns_empty_when_unsupported():
    assert parse_slots("unknown command\nready> ") == []


# -- a modem that behaves ------------------------------------------------

class FakeHMI:
    """Accepts the upload sequence and tracks what it was told."""

    def __init__(self, *, echo=True, fail_crc=False):
        self.echo = echo
        self.fail_crc = fail_crc
        self.written = bytearray()
        self.commands: list[str] = []
        self.expected_size = None

    def command(self, text, timeout=None):
        self.commands.append(text)
        if text.startswith("slots"):
            return SLOTS_REPLY
        if text.startswith("fwbegin"):
            _, size, _crc = text.split()
            self.expected_size = int(size, 16)
            return f"erasing slot 1...\nready for {self.expected_size} bytes\nready> "
        if text.startswith("fwd "):
            payload = bytes.fromhex(text[4:])
            self.written += payload
            # Real firmware echoes the line back before replying.
            prefix = text + "\n" if self.echo else ""
            return f"{prefix}{len(self.written)}\nready> "
        if text.startswith("fwend"):
            if self.fail_crc:
                return "CRC mismatch: got 00000000 want FFFFFFFF\nready> "
            return "verified, crc DEADBEEF; reboot to install\nready> "
        return "unknown command\nready> "


def make_container(nbytes=2048, version=26072303):
    image = bytes((i * 7 + 3) & 0xFF for i in range(nbytes))
    return Container.build(image, version=version, hardware_id=240719)


def test_install_writes_the_whole_image():
    hmi, c = FakeHMI(), make_container()
    install(hmi, c, chunk=128)
    assert bytes(hmi.written) == c.image
    assert hmi.commands[0].startswith("fwbegin")
    assert hmi.commands[-1].startswith("fwend")


def test_install_reports_progress_monotonically():
    hmi, c = FakeHMI(), make_container()
    seen = []
    install(hmi, c, chunk=256, progress=lambda done, total: seen.append(done))
    assert seen == sorted(seen)
    assert seen[-1] == len(c.image)


def test_install_survives_echoed_hex_containing_error_words():
    """The reply echoes the hex, which can spell 'bad' or 'fail'.

    A substring check for those words aborts a perfectly good upload; only the
    reported byte count is a reliable signal.
    """
    image = bytes.fromhex("6badf81420" + "fa11ed00") * 64
    image += b"\x00" * (-len(image) % 1024)
    c = Container.build(image, version=1, hardware_id=240719)
    hmi = FakeHMI(echo=True)
    install(hmi, c, chunk=128)
    assert bytes(hmi.written) == c.image


def test_install_rejects_crc_failure():
    hmi, c = FakeHMI(fail_crc=True), make_container()
    with pytest.raises(NetflashError, match="fwend rejected"):
        install(hmi, c, chunk=128)


@pytest.mark.parametrize("chunk", [6, 130, -4, 0])
def test_install_rejects_unusable_chunk_size(chunk):
    """Flash is programmed a word at a time, so chunks must be word-aligned."""
    with pytest.raises(NetflashError, match="multiple of 4"):
        install(FakeHMI(), make_container(), chunk=chunk)


def test_install_stops_when_a_chunk_is_not_acknowledged():
    class Silent(FakeHMI):
        def command(self, text, timeout=None):
            if text.startswith("fwd "):
                return "ready> "  # no byte count
            return super().command(text, timeout)

    with pytest.raises(NetflashError, match="chunk at 0"):
        install(Silent(), make_container(), chunk=128)


def test_supported_detects_stock_firmware():
    class Stock(FakeHMI):
        def command(self, text, timeout=None):
            return "unknown command\nready> "

    assert supported(FakeHMI()) is True
    assert supported(Stock()) is False
