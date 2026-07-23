"""Command-line interface.

    nprflash probe                                   identify the attached bootloader
    nprflash info    <container.nfw>                 show container metadata
    nprflash build   <image.bin> -v VER -w HW -o OUT wrap an image as a .nfw container
    nprflash flash   <container.nfw>                 write a container to the device
    nprflash console                                 read the runtime serial console
    nprflash netflash <container.nfw> --host H       install over Ethernet

`probe`, `info` and `console` are read-only. `flash` and `netflash` write.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import time

from . import __doc__ as _pkg_doc
from . import console as _console
from .bootloader import Bootloader, HardwareMismatch
from .container import BLOCK_SIZE, Container, ContainerError
from .protocol import CommandFailed, ProtocolError
from .hmi import HMI, HMIError
from .netflash import NetflashError, install, parse_slots, supported
from .netflash import __doc__ as _netflash_doc
from .transport import SerialTransport, TransportError, find_debug_probes


def _progress(done: int, total: int) -> None:
    """A single rewritten line."""
    width = 32
    filled = width * done // total if total else width
    pct = 100 * done // total if total else 100
    end = "\n" if done >= total else ""
    print(f"\r  [{'#' * filled}{'.' * (width - filled)}] "
          f"{pct:3d}%  {done}/{total} bytes", end=end, flush=True)


def _load(path: pathlib.Path) -> Container:
    try:
        return Container.parse(path.read_bytes())
    except (OSError, ContainerError) as ex:
        raise SystemExit(f"could not read {path}: {ex}")


# -- subcommands ----------------------------------------------------------

def cmd_probe(args: argparse.Namespace) -> int:
    with SerialTransport(args.port, timeout=args.timeout) as t:
        print(f"port: {t.port}")
        print(Bootloader(t).identify())
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    container = _load(args.container)
    print(container.describe())
    if not container.crc_is_valid():
        print("\nWARNING: the stored checksum does not match the image. The "
              "device would not detect this until the final commit, by which "
              "point it has already written everything.", file=sys.stderr)
        return 1
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    try:
        image = args.image.read_bytes()
    except OSError as ex:
        raise SystemExit(f"could not read {args.image}: {ex}")

    container = Container.build(image, version=args.version,
                                hardware_id=args.hardware)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(container.to_bytes())
    print(container.describe())
    print(f"\nwrote {args.output}")
    if len(image) != len(container.image):
        print(f"  (image padded {len(image)} -> {len(container.image)} bytes, "
              f"a whole number of {BLOCK_SIZE}-byte blocks)")
    return 0


def cmd_flash(args: argparse.Namespace) -> int:
    container = _load(args.container)
    print(container.describe())
    if not container.crc_is_valid():
        print("\nrefusing to flash: the container's checksum does not match its "
              "image, so the device would reject it only after writing "
              "everything.", file=sys.stderr)
        return 1

    try:
        transport = SerialTransport(args.port, timeout=args.timeout)
    except TransportError as ex:
        print(str(ex), file=sys.stderr)
        return 1

    print(f"port:               {transport.port}")
    with transport as t:
        bl = Bootloader(t)
        try:
            info = bl.identify()
        except (ProtocolError, TransportError) as ex:
            print(f"\nidentify failed: {ex}\nRefusing to write.", file=sys.stderr)
            return 1
        print(info)

        if info.hardware_id != container.hardware_id and not args.force:
            print(f"\nhardware ID mismatch: device reports {info.hardware_id}, "
                  f"image is built for {container.hardware_id}.\n"
                  "Refusing to flash. Use --force only for a deliberate "
                  "negative test.", file=sys.stderr)
            return 1

        if not args.yes:
            print(f"\nAbout to write {len(container.image)} bytes to the "
                  "application partition. The bootloader region is not "
                  "addressable by this protocol, and a bad image is recoverable "
                  "by reflashing.")
            try:
                if input("Type FLASH to continue: ").strip() != "FLASH":
                    return 1
            except (EOFError, KeyboardInterrupt):
                print("\naborted", file=sys.stderr)
                return 1

        started = time.monotonic()
        try:
            bl.flash(container, progress=_progress, check_hardware=not args.force)
        except HardwareMismatch as ex:
            print(f"\n{ex}", file=sys.stderr)
            return 1
        except CommandFailed as ex:
            print(f"\n{ex}", file=sys.stderr)
            if ex.status.name == "CRC_FAIL":
                print("The image was written before this was detected; the "
                      "application partition is now dirty and must be "
                      "reflashed.", file=sys.stderr)
            return 1
        except (ProtocolError, TransportError) as ex:
            print(f"\nflash failed: {ex}", file=sys.stderr)
            return 1

    print(f"\nflashed {len(container.image)} bytes in "
          f"{time.monotonic() - started:.1f}s")
    print(f"""
Power-cycle onto the main supply with the micro-USB DISCONNECTED, then confirm
the unit is running this image -- an accepted flash is not the same as a
booting one:

    nprflash console --send version

This uses the UART console on Connector 1 (via a Pi Debug Probe or USB-TTL
adapter), NOT the micro-USB port.

Expect  ->  NPR FW {container.version}""")
    return 0


# -- entry point ----------------------------------------------------------

def cmd_netflash(args: argparse.Namespace) -> int:
    """Install a container over Ethernet, leaving the running image in place."""
    container = _load(args.container)
    print(f"Firmware version:   {container.version}")
    print(f"Hardware ID:        {container.hardware_id}")
    print(f"Image size:         {len(container.image)} bytes")
    print(f"host:               {args.host}")

    try:
        with HMI(args.host, timeout=args.timeout) as hmi:
            if hmi.authenticate(hmi.banner(), args.password):
                print("authenticated")
            if not supported(hmi):
                print("\nThis modem's firmware does not provide the upload commands.\n"
                      "Install over the USB bootloader instead:  nprflash flash",
                      file=sys.stderr)
                return 1

            before = hmi.command("slots")
            for slot in parse_slots(before):
                print(f"  slot{slot['slot']} @{slot['address']:08X} "
                      f"version {slot['version']} size {slot['size']}")

            if not args.yes:
                if input("proceed? [y/N] ").strip().lower() not in ("y", "yes"):
                    return 1

            print("uploading; the modem stalls briefly on the first chunk to erase")
            # A full image is several hundred chunks; redrawing per chunk floods
            # any log that does not honour carriage returns, so throttle it.
            total = len(container.image)
            step = max(args.chunk, total // 64)

            def throttled(done: int, size: int, _last=[0]) -> None:
                if done - _last[0] >= step or done >= size:
                    _last[0] = done
                    _progress(done, size)

            install(hmi, container, chunk=args.chunk, progress=throttled)
            print("verified; the modem will swap slots on the next reset")

            if args.reboot:
                hmi.command("reboot", timeout=5.0)
                print("reboot sent")
            else:
                print("send 'reboot' when ready to install")
    except (HMIError, NetflashError) as ex:
        print(f"\n{ex}", file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="nprflash", description=_pkg_doc,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    def with_port(p):
        p.add_argument("-p", "--port", default=None,
                       help="serial device; default is to autodetect 0483:5740")
        p.add_argument("--timeout", type=float, default=5.0,
                       help="per-reply timeout in seconds (default 5)")
        return p

    with_port(sub.add_parser("probe", help="identify the attached bootloader"
                             )).set_defaults(func=cmd_probe)

    p = sub.add_parser("info", help="show .nfw container metadata")
    p.add_argument("container", type=pathlib.Path, help=".nfw container")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("build", help="wrap a firmware image as a .nfw container")
    p.add_argument("image", type=pathlib.Path, help="raw firmware image (.bin)")
    p.add_argument("-v", "--version", type=int, required=True, help="YYMMDDRR")
    p.add_argument("-w", "--hardware", type=int, required=True, help="hardware ID")
    p.add_argument("-o", "--output", type=pathlib.Path, required=True,
                   help="container to write (.nfw)")
    p.set_defaults(func=cmd_build)

    p = with_port(sub.add_parser("flash", help="write a container to the device"))
    p.add_argument("container", type=pathlib.Path, help=".nfw container")
    p.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    p.add_argument("--force", action="store_true",
                   help="flash despite a hardware-ID mismatch (negative tests only)")
    p.set_defaults(func=cmd_flash)

    p = sub.add_parser("netflash", help="install a container over Ethernet",
                       description=_netflash_doc,
                       formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("container", type=pathlib.Path, help=".nfw container")
    p.add_argument("--host", required=True, help="modem address, e.g. 192.168.0.253")
    p.add_argument("--timeout", type=float, default=20.0,
                   help="per-reply timeout in seconds (default 20)")
    p.add_argument("--chunk", type=int, default=128,
                   help="bytes per upload line (default 128)")
    p.add_argument("--password", default=None,
                   help="passphrase, if the modem's HMI requires one")
    p.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    p.add_argument("--reboot", action="store_true",
                   help="reboot immediately so the swap happens now")
    p.set_defaults(func=cmd_netflash)

    p = sub.add_parser("console", help="read the runtime serial console",
                       description=_console.__doc__,
                       formatter_class=argparse.RawDescriptionHelpFormatter)
    _console.add_arguments(p)
    p.set_defaults(func=lambda a: _console.run(a))
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except TransportError as ex:
        print(str(ex), file=sys.stderr)
        if find_debug_probes():
            print("\n(A Debug Probe is attached; it is not a flash target.)",
                  file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
