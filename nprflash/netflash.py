"""Install a container over Ethernet, via the modem's telnet HMI.

Requires firmware providing the upload commands ``fwbegin``, ``fwd``, ``fwend``
and ``slots``. Stock firmware does not have them, and this will simply report
that the modem does not support it. Use the USB bootloader path for those.

How it works, and why it is safe: the image is written into the modem's spare
image slot, never over the one it is running. The modem recomputes the CRC over
what actually landed in flash and refuses the update if it disagrees. Only then
are the boot flags moved, and the swap happens on the next reset. If the new
image fails to validate the modem simply keeps booting the old one, and the
replaced image remains in the spare slot afterwards.
"""

from __future__ import annotations

import re
import struct
from typing import Callable

from .container import Container
from .hmi import HMI, HMIError

DEFAULT_CHUNK = 128

_SLOT_LINE = re.compile(
    r"slot(?P<n>[01])\s+@(?P<addr>[0-9A-Fa-f]+)\s+ver\s+(?P<ver>\d+)\s+size\s+(?P<size>\d+)")


class NetflashError(Exception):
    """The modem rejected the update, or does not support one."""


def parse_slots(text: str) -> list[dict]:
    """Pull the slot table out of a ``slots`` reply."""
    slots = []
    for m in _SLOT_LINE.finditer(text):
        slots.append({
            "slot": int(m.group("n")),
            "address": int(m.group("addr"), 16),
            "version": int(m.group("ver")),
            "size": int(m.group("size")),
        })
    return slots


def supported(hmi: HMI) -> bool:
    """True if this firmware offers the upload commands."""
    reply = hmi.command("slots")
    return "unknown command" not in reply and "descriptor" in reply


def _expect_progress(reply: str, expected: int) -> bool:
    """Confirm the modem reported the new byte count.

    The reply echoes back the hex just sent, so scanning it for words like
    "bad" or "fail" gives false positives — hex readily contains them, e.g.
    ``...6badf81420...``. The reported count is the reliable signal.
    """
    return any(line.strip() == str(expected) for line in reply.splitlines())


def install(hmi: HMI, container: Container, *,
            chunk: int = DEFAULT_CHUNK,
            progress: Callable[[int, int], None] | None = None,
            erase_timeout: float = 30.0) -> str:
    """Upload and stage ``container``. Returns the modem's final reply.

    The caller must reboot the modem afterwards for the swap to happen.
    """
    image = container.image
    total = len(image)
    if total % 4:
        raise NetflashError("image length must be a multiple of 4 bytes")
    if chunk % 4 or chunk <= 0:
        raise NetflashError("chunk size must be a positive multiple of 4")

    crc = struct.unpack("<I", container.crc)[0]

    reply = hmi.command(f"fwbegin {total:X} {crc:X}", timeout=erase_timeout)
    if "ready for" not in reply:
        raise NetflashError(f"fwbegin rejected: {reply.strip()}")

    sent = 0
    for offset in range(0, total, chunk):
        piece = image[offset:offset + chunk]
        # The first chunk carries the erase, so allow it the longer timeout.
        reply = hmi.command("fwd " + piece.hex(),
                            timeout=erase_timeout if offset == 0 else None)
        expected = sent + len(piece)
        if not _expect_progress(reply, expected):
            raise NetflashError(
                f"chunk at {offset} failed (expected offset {expected}): {reply.strip()}")
        sent = expected
        if progress:
            progress(sent, total)

    reply = hmi.command(f"fwend {container.version:X}", timeout=erase_timeout)
    if "verified" not in reply:
        raise NetflashError(f"fwend rejected: {reply.strip()}")
    return reply
