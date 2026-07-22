"""Serial transport to the NPR bootloader.

The bootloader exposes a USB CDC virtual COM port (``0483:5740``) that appears
**only while the unit is running the bootloader** -- power it from the micro-USB
with the main supply disconnected. Under normal 12 V operation the application
runs, which has no USB stack at all, and this port is absent.

Discovery deliberately matches on USB VID/PID rather than picking the first
``/dev/ttyACM*``: a Raspberry Pi Debug Probe used for SWD or the console shares
that namespace and must never be flashed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import serial
from serial.tools import list_ports

from .protocol import DELIMITER

#: STMicroelectronics USB CDC virtual COM port, as exposed by the bootloader.
BOOTLOADER_VID = 0x0483
BOOTLOADER_PIDS = (0x5740,)

#: Raspberry Pi Debug Probe -- SWD/UART tooling, never a flash target.
DEBUG_PROBE_VID = 0x2E8A
DEBUG_PROBE_PID = 0x000C


class TransportError(Exception):
    """The port could not be found, opened, or read within the timeout."""


@dataclass(frozen=True)
class PortInfo:
    device: str
    description: str


def find_bootloader_ports() -> list[PortInfo]:
    """All attached bootloader VCPs, in enumeration order."""
    return [PortInfo(p.device, p.description or "")
            for p in list_ports.comports()
            if p.vid == BOOTLOADER_VID and p.pid in BOOTLOADER_PIDS]


def find_debug_probes() -> list[PortInfo]:
    """Attached Raspberry Pi Debug Probes, so they can be excluded by name."""
    return [PortInfo(p.device, p.description or "")
            for p in list_ports.comports()
            if p.vid == DEBUG_PROBE_VID and p.pid == DEBUG_PROBE_PID]


def autodetect() -> str:
    """Locate exactly one bootloader VCP, or explain why we cannot."""
    ports = find_bootloader_ports()
    if not ports:
        lines = [f"No bootloader VCP ({BOOTLOADER_VID:04x}:"
                 f"{BOOTLOADER_PIDS[0]:04x}) found."]
        probes = find_debug_probes()
        if probes:
            lines.append(
                f"A Raspberry Pi Debug Probe is present ({probes[0].device}), "
                "but that is the SWD/UART probe, not the target.")
        lines.append("The bootloader VCP appears only when the unit is powered "
                     "from the micro-USB with the main supply DISCONNECTED.")
        raise TransportError("\n".join(lines))
    return ports[0].device


class SerialTransport:
    """Framed request/response over the bootloader's serial port."""

    def __init__(self, port: str | None = None, *, timeout: float = 5.0):
        self.port = port or autodetect()
        self.timeout = timeout
        self._serial: serial.Serial | None = None

    # -- lifecycle -------------------------------------------------------

    def open(self) -> None:
        try:
            # A short read timeout keeps recv_frame responsive; the real bound
            # is the deadline it enforces itself.
            self._serial = serial.Serial(self.port, timeout=0.1)
        except serial.SerialException as ex:
            raise TransportError(f"could not open {self.port}: {ex}") from ex
        self._serial.reset_input_buffer()
        self._serial.reset_output_buffer()

    def close(self) -> None:
        if self._serial is not None:
            self._serial.close()
            self._serial = None

    def __enter__(self) -> "SerialTransport":
        self.open()
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # -- framing ---------------------------------------------------------

    @property
    def _port(self) -> serial.Serial:
        if self._serial is None:
            raise TransportError("transport is not open")
        return self._serial

    def send_frame(self, frame: bytes) -> None:
        self._port.write(frame)
        self._port.flush()

    def recv_frame(self) -> bytes:
        """Read up to and including the next delimiter.

        Unlike the vendor tool's unbounded loop, this enforces a deadline, so a
        device that stops responding raises instead of hanging forever.
        """
        deadline = time.monotonic() + self.timeout
        buf = bytearray()
        while time.monotonic() < deadline:
            chunk = self._port.read(1)
            if not chunk:
                continue
            if chunk == DELIMITER:
                return bytes(buf)
            buf += chunk
        raise TransportError(
            f"timed out after {self.timeout:g}s waiting for a reply"
            + (f" ({len(buf)} bytes received without a delimiter)" if buf else ""))
