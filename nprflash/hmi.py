"""Client for the modem's telnet HMI.

The modem listens on TCP 23, prompts with ``ready> ``, echoes what it receives,
accepts a single session at a time and drops it after 300 s idle. It opens by
sending three telnet option negotiations; we negotiate nothing and simply strip
IAC sequences out of the stream.

Only reachable from a host on the modem's own LAN segment.
"""

from __future__ import annotations

import socket
import time

PROMPT = b"ready> "

IAC = 0xFF
_NEGOTIATE = (0xFB, 0xFC, 0xFD, 0xFE)  # WILL, WONT, DO, DONT


class HMIError(Exception):
    """The modem could not be reached, or did not answer as expected."""


def strip_iac(chunk: bytes, pending: bytes = b"") -> tuple[bytes, bytes]:
    """Remove telnet control sequences, tolerating splits across reads.

    Returns the cleaned bytes and any incomplete trailing sequence, which the
    caller passes back in on the next call.
    """
    data = pending + chunk
    out = bytearray()
    i = 0
    while i < len(data):
        if data[i] != IAC:
            out.append(data[i])
            i += 1
            continue
        if i + 1 >= len(data):
            return bytes(out), bytes(data[i:])
        command = data[i + 1]
        if command == IAC:  # escaped literal 0xFF
            out.append(IAC)
            i += 2
        elif command in _NEGOTIATE:
            if i + 2 >= len(data):
                return bytes(out), bytes(data[i:])
            i += 3
        else:
            i += 2
    return bytes(out), b""


class HMI:
    """A single telnet session with one modem."""

    def __init__(self, host: str, port: int = 23, timeout: float = 20.0) -> None:
        try:
            self._sock = socket.create_connection((host, port), timeout=timeout)
        except OSError as ex:
            raise HMIError(f"cannot reach {host}:{port}: {ex}") from ex
        self._sock.settimeout(0.5)
        self._pending = b""
        self.timeout = timeout

    def read_reply(self, timeout: float | None = None) -> str:
        """Read until the modem re-prompts, it closes, or the deadline passes."""
        deadline = time.monotonic() + (timeout or self.timeout)
        buf = b""
        while time.monotonic() < deadline:
            try:
                chunk = self._sock.recv(4096)
            except socket.timeout:
                continue
            except OSError as ex:
                raise HMIError(f"read failed: {ex}") from ex
            if not chunk:
                break  # modem closed the connection
            clean, self._pending = strip_iac(chunk, self._pending)
            buf += clean
            if buf.endswith(PROMPT):
                break
        return buf.decode("utf-8", "replace")

    def command(self, text: str, timeout: float | None = None) -> str:
        try:
            self._sock.sendall(text.encode("ascii") + b"\r\n")
        except OSError as ex:
            raise HMIError(f"write failed: {ex}") from ex
        return self.read_reply(timeout)

    def banner(self, timeout: float = 6.0) -> str:
        return self.read_reply(timeout)

    def close(self) -> None:
        try:
            self._sock.sendall(b"exit\r\n")
            time.sleep(0.2)
        except OSError:
            pass
        self._sock.close()

    def __enter__(self) -> "HMI":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
