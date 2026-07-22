"""Firmware tooling for the NPR-H 3.0 packet radio modem.

Build `.nfw` firmware containers, flash them over the bootloader's USB serial
port, and read the runtime console to confirm what the unit is actually running.

Not affiliated with or endorsed by the manufacturer.
"""

__version__ = "1.0.0"

from .bootloader import Bootloader, DeviceInfo, HardwareMismatch
from .container import BLOCK_SIZE, Container, ContainerError
from .protocol import CommandFailed, Opcode, ProtocolError, Status
from .transport import SerialTransport, TransportError, autodetect

__all__ = [
    "BLOCK_SIZE", "Bootloader", "CommandFailed", "Container", "ContainerError",
    "DeviceInfo", "HardwareMismatch", "Opcode", "ProtocolError",
    "SerialTransport", "Status", "TransportError", "autodetect", "__version__",
]
