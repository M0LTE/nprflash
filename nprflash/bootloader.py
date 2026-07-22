"""Driving the Localino NPR bootloader: identify, and flash.

The flash sequence is::

    BL_INFO                      identify the device (read-only)
    FW_INFO   hw, ver, len, crc  announce the image; hardware ID checked HERE
    FW_DATA   hw, ver, offset    one 1024-byte block at a time
    ...
    FW_FINALIZE hw, ver          commit; CRC checked HERE, after the write

Two consequences of that ordering are worth keeping in mind:

* A wrong-hardware image is rejected at ``FW_INFO`` and the installed firmware
  is untouched.
* A CRC mismatch is only caught at ``FW_FINALIZE``, once every block has been
  written. The application partition is left dirty and must be reflashed. The
  bootloader's ten-attempt retry then fallback to USB receive mode is what
  makes that recoverable rather than fatal.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from . import protocol
from .container import BLOCK_SIZE, Container
from .transport import SerialTransport

#: Called as progress(bytes_done, bytes_total) during a flash.
ProgressFn = Callable[[int, int], None]


@dataclass(frozen=True)
class DeviceInfo:
    hardware_id: int
    bootloader_version: int

    def __str__(self) -> str:
        return (f"Hardware ID:        {self.hardware_id}\n"
                f"Bootloader version: {self.bootloader_version}")


class HardwareMismatch(Exception):
    """The image was built for different hardware than the device reports."""

    def __init__(self, device_id: int, image_id: int):
        self.device_id = device_id
        self.image_id = image_id
        super().__init__(
            f"hardware ID mismatch: device reports {device_id}, "
            f"image is built for {image_id}")


class Bootloader:
    """A conversation with the bootloader over an open transport."""

    def __init__(self, transport: SerialTransport):
        self.transport = transport

    def _exchange(self, payload: bytes) -> bytes:
        self.transport.send_frame(protocol.encode_frame(payload))
        return protocol.decode_frame(self.transport.recv_frame())

    # -- read-only -------------------------------------------------------

    def identify(self) -> DeviceInfo:
        """Send BL_INFO. Writes nothing, so it is safe to call at any time."""
        reply = self._exchange(protocol.bl_info())
        hardware_id, bootloader_version = protocol.parse_bl_info(reply)
        return DeviceInfo(hardware_id, bootloader_version)

    # -- flashing --------------------------------------------------------

    def flash(self, container: Container, *,
              progress: Optional[ProgressFn] = None,
              check_hardware: bool = True) -> DeviceInfo:
        """Write a container's firmware image to the application partition.

        Returns the DeviceInfo read before flashing. Raises CommandFailed with
        the device's own status code if any stage is rejected.

        `check_hardware=False` exists for deliberate negative testing; leave it
        on otherwise, since the check costs one read-only exchange and prevents
        a pointless write.
        """
        info = self.identify()
        if check_hardware and info.hardware_id != container.hardware_id:
            raise HardwareMismatch(info.hardware_id, container.hardware_id)

        total = len(container.image)

        reply = self._exchange(protocol.fw_info(
            container.hardware_id, container.version, total, container.crc))
        protocol.parse_status(reply, protocol.Opcode.FW_INFO)

        if progress:
            progress(0, total)
        for offset in range(0, total, BLOCK_SIZE):
            chunk = container.image[offset:offset + BLOCK_SIZE]
            reply = self._exchange(protocol.fw_data(
                container.hardware_id, container.version, offset, chunk))
            protocol.parse_status(reply, protocol.Opcode.FW_DATA)
            if progress:
                progress(offset + len(chunk), total)

        reply = self._exchange(protocol.fw_finalize(
            container.hardware_id, container.version))
        protocol.parse_status(reply, protocol.Opcode.FW_FINALIZE)
        return info
