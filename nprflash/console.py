#!/usr/bin/env python3
"""NPR console over the Raspberry Pi Debug Probe's UART.

The application has no USB stack: the micro-USB port belongs to the bootloader,
so once firmware is running the only console is USART2 (PD5/PD6) on rear
Connector 1 at 921600 8N1. Reach it with any 3.3 V USB-TTL adapter, or the UART
side of a Raspberry Pi Debug Probe, which enumerates as /dev/ttyACM*.

Everything received is timestamped and written to a log file as well as stdout,
so a boot banner captured during a power-cycle survives for later comparison --
which is the whole point when the firmware version is the only evidence of
which image is actually running.

Two firmware quirks are handled here, both observed on real hardware:

  * The console reads one byte per main-loop pass off the raw RXNE flag, with
    no interrupt, no DMA and no FIFO. Anything written faster than the loop
    turns over is silently dropped, so sends are paced per character.
  * A send whose terminating CR gets dropped leaves a fragment in the firmware's
    line buffer that silently prefixes the next command. A bare CR is sent first
    to clear it.

Three things about the link itself:

  * OPENING THIS PORT RESETS THE UNIT. Every connection produces a fresh
    bootloader banner, and the bootloader only retries an application ten times
    before dropping into USB-receive mode. Batch work into ONE connection
    (--send is repeatable) rather than looping one-shot invocations. The exact
    mechanism is unproven -- the probe's UART header has no reset line -- but
    the effect is consistent and costly.
  * Because of that reset, commands are not sent until the "ready>" prompt
    appears, or --send-after seconds elapse, whichever comes first.
  * The probe's CDC-UART bridge only forwards data while the host holds the
    port open with DTR asserted; --no-dtr yields total silence. It also
    re-enumerates on USB occasionally, which is survived by reopening.

Examples:
    nprflash console --seconds 60                     # catch a boot banner
    nprflash console --send version --seconds 20      # what is it running?
    nprflash console --send "display config" --seconds 20
"""

import argparse
import datetime
import glob
import pathlib
import sys
import time

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    sys.exit(
        "pyserial not found. Install this package first:\n"
        "  pip install -e ."
    )

RPI_DEBUG_PROBE_VID = 0x2E8A
RPI_DEBUG_PROBE_PID = 0x000C

NOTHING_RECEIVED_HELP = """Nothing received. Things to check, cheapest first:
  - Is the unit actually powered? The console is silent if it is not.
  - The banner only prints at boot -- power-cycle while listening.
  - TX/RX crossover: probe UART TX -> NPR Connector 1 RX, and
    probe RX -> NPR TX. If unsure, swap them; it is harmless.
  - Grounds common between probe and NPR.
  - Wrong connector: the UART is the probe's 'U' JST, not 'D'."""


def find_port(quiet: bool = False):
    """Prefer the Debug Probe's CDC interface; fall back to any ttyACM.

    Returns None rather than exiting, so a reconnect loop can keep waiting.
    """
    for port in list_ports.comports():
        if port.vid == RPI_DEBUG_PROBE_VID and port.pid == RPI_DEBUG_PROBE_PID:
            return port.device
    acm = sorted(glob.glob("/dev/ttyACM*"))
    if acm:
        if not quiet:
            print(f"note: no Debug Probe CDC found, falling back to {acm[0]}",
                  file=sys.stderr)
        return acm[0]
    return None


def now() -> str:
    return datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]


def add_arguments(ap: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Register the console options on `ap` (used as a CLI subcommand)."""
    ap.add_argument("--port", default="auto", help="serial device, or 'auto'")
    ap.add_argument("--baud", type=int, default=921600,
                    help="baud rate (firmware sets 921600 8N1)")
    ap.add_argument("--seconds", type=float, default=30.0,
                    help="how long to listen; 0 = until Ctrl-C")
    ap.add_argument("--send", action="append", default=[], metavar="CMD",
                    help="command to send once connected (repeatable)")
    ap.add_argument("--char-delay", type=float, default=0.02, metavar="SEC",
                    help="pause between characters when sending (default 0.02); "
                         "the firmware drops burst-written input")
    ap.add_argument("--send-after", type=float, default=6.0, metavar="SEC",
                    help="max wait for the 'ready>' prompt before sending "
                         "(connecting resets the unit; default 6)")
    ap.add_argument("--no-flush", action="store_true",
                    help="do not send a leading CR to clear a stale input line")
    ap.add_argument("--no-dtr", action="store_true",
                    help="open with DTR/RTS deasserted. NOTE: with the Raspberry "
                         "Pi Debug Probe this stops data being forwarded at all")
    ap.add_argument("--no-reconnect", action="store_true",
                    help="abort instead of reopening if the probe re-enumerates")
    ap.add_argument("--log", default=None, help="log file path")
    return ap


def run(args) -> int:
    """Open the console, optionally send commands, and log what comes back."""
    logdir = pathlib.Path.cwd() / "logs"
    logdir.mkdir(exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    logpath = pathlib.Path(args.log) if args.log else logdir / f"console-{stamp}.log"

    print(f"port   {args.port} @ {args.baud} 8N1")
    print(f"log    {logpath}")
    if args.send:
        print(f"send   {args.send}")
    print(f"listen {'until Ctrl-C' if args.seconds == 0 else f'{args.seconds:g}s'}")
    print("-" * 60, flush=True)

    deadline = None if args.seconds == 0 else time.monotonic() + args.seconds
    saw_any = False
    sent_already = False
    pending = ""
    reconnects = 0

    def running() -> bool:
        return deadline is None or time.monotonic() < deadline

    def emit(line: str, log) -> None:
        log.write(f"{now()} {line}\n")
        log.flush()
        print(line, flush=True)

    with open(logpath, "w", encoding="utf-8", errors="replace") as log:
        try:
            while running():
                port = find_port(quiet=reconnects > 0) if args.port == "auto" else args.port
                if port is None:
                    if args.no_reconnect:
                        print(NOTHING_RECEIVED_HELP, file=sys.stderr)
                        return 1
                    time.sleep(0.5)
                    continue

                try:
                    # Leave DTR/RTS asserted (pyserial's default). The Raspberry
                    # Pi Debug Probe's CDC-UART bridge only forwards received
                    # data while the host holds the port open with DTR raised --
                    # deasserting it yields total silence, which is easily
                    # misread as a dead target. See --no-dtr.
                    ser = serial.Serial()
                    ser.port = port
                    ser.baudrate = args.baud
                    ser.timeout = 0.1
                    if args.no_dtr:
                        ser.dtr = False
                        ser.rts = False
                    ser.open()
                except serial.SerialException as ex:
                    if args.no_reconnect:
                        sys.exit(f"could not open {port}: {ex}")
                    time.sleep(0.5)
                    continue

                try:
                    with ser:
                        # Opening the port resets the unit (see the module
                        # docstring), so anything sent immediately is swallowed
                        # by the boot that follows. Wait for the "ready>" prompt
                        # -- or a fallback timeout, since an already-idle unit
                        # will not emit one unprompted -- before typing.
                        settle_until = time.monotonic() + args.send_after
                        prompt_seen = False

                        def send_commands():
                            if not args.no_flush:
                                # Clear any half-typed line left in the
                                # firmware's current_rx_line buffer -- a stale
                                # fragment silently prefixes the next command.
                                ser.write(b"\r")
                                ser.flush()
                                time.sleep(0.3)
                                ser.reset_input_buffer()
                            for cmd in args.send:
                                for ch in cmd + "\r\n":
                                    ser.write(ch.encode())
                                    ser.flush()
                                    time.sleep(args.char_delay)
                                log.write(f"{now()} [sent] {cmd}\n")
                                print(f"\033[36m[sent]\033[0m {cmd}", flush=True)
                                time.sleep(0.4)

                        while running():
                            chunk = ser.read(4096)
                            if chunk:
                                saw_any = True
                                pending += chunk.decode("utf-8", errors="replace")
                                *lines, pending = pending.split("\n")
                                for line in lines:
                                    emit(line.rstrip("\r"), log)
                                if "ready>" in pending or "ready>" in "".join(lines):
                                    prompt_seen = True

                            if args.send and not sent_already:
                                if prompt_seen or time.monotonic() >= settle_until:
                                    send_commands()
                                    sent_already = True

                except serial.SerialException as ex:
                    # The Debug Probe drops off USB and comes back; that should
                    # not end a capture that is waiting for a power-cycle.
                    if args.no_reconnect:
                        emit(f"[serial error] {ex}", log)
                        return 1
                    reconnects += 1
                    msg = f"[probe re-enumerated, reopening: {ex}]"
                    log.write(f"{now()} {msg}\n")
                    log.flush()
                    print(f"\033[33m{msg}\033[0m", file=sys.stderr, flush=True)
                    time.sleep(0.5)
                    continue

        except KeyboardInterrupt:
            print("\n(interrupted)", file=sys.stderr)

        if pending:
            # A prompt like "ready> " arrives with no trailing newline.
            emit(pending, log)

    print("-" * 60)
    if reconnects:
        print(f"note: reopened the port {reconnects}x during this capture",
              file=sys.stderr)
    if not saw_any:
        print(NOTHING_RECEIVED_HELP, file=sys.stderr)
        return 1
    print(f"logged to {logpath}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    return run(add_arguments(ap).parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
