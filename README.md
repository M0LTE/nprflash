# nprflash

Firmware tooling for the **NPR-H 3.0** packet radio modem. Builds `.nfw`
containers, flashes them over the bootloader's USB serial port, and reads the
runtime console so you can confirm what the unit is actually running.

Runs natively on Linux, macOS and Windows.

## Install

Requires Python 3.10 or newer. The only dependency is `pyserial`.

### Debian, Ubuntu and Raspberry Pi OS: apt

The recommended route, because `apt upgrade` then keeps it current along with everything else:

```sh
curl -fsSL https://packet-net.github.io/apt/pubkey.asc | sudo gpg --dearmor -o /usr/share/keyrings/packet-net.gpg
echo "deb [signed-by=/usr/share/keyrings/packet-net.gpg] https://packet-net.github.io/apt ./" | sudo tee /etc/apt/sources.list.d/packet-net.list
sudo apt update
sudo apt install nprflash
```

One package covers `amd64`, `arm64` and `armhf`: nprflash is pure Python and runs on the Python already on the machine, so there is nothing architecture-specific in it and pyserial arrives as the distribution's own `python3-serial`. The same [packet-net apt repository](https://github.com/packet-net/apt) carries the rest of the Packet.NET packages, so the lines above are worth having anyway.

Debian 12 (bookworm), Ubuntu 22.04, Raspberry Pi OS bookworm and later. Older releases ship Python 3.9, so apt will decline the package there and say which Python it found; use pip below instead. The `.deb` is attached to each [release](https://github.com/M0LTE/nprflash/releases/latest) as well, for installing one by hand with `sudo apt install ./nprflash_<version>_all.deb`.

### Everything else: pip

Works the same on Linux, macOS and Windows:

```sh
pipx install git+https://github.com/M0LTE/nprflash     # or: pip install git+https://...
nprflash --help
```

`pipx` puts it in its own environment, which is what a recent distribution's Python will insist on anyway. From a clone it is `pipx install .` or `pip install -e .`, and `python3 -m nprflash probe` runs it straight out of the checkout with only `python3-serial` installed.

### Serial port permission

On Linux you need permission for the serial port, which is usually membership of `dialout` (or `uucp` on Arch):

```sh
sudo usermod -aG dialout "$USER"    # log out and back in
```

## Power states matter

The unit exposes exactly one interface at a time, and which one depends on how
it is powered:

| Powered by | What runs | What you get |
|---|---|---|
| micro-USB, main supply **disconnected** | bootloader | USB serial port `0483:5740`: `probe`, `flash` |
| main supply, micro-USB **disconnected** | application | USART2 console on Connector 1: `console` |

So a silent console is normal while USB-powered, and a missing serial port is
normal while running on the main supply. Check which supply is connected before
concluding anything is broken.

## Flashing

Put the unit in its bootloader, powered from the micro-USB with the main
supply disconnected, then:

```sh
nprflash probe                       # identify: hardware ID, bootloader version
nprflash flash firmware.nfw
```

Reconnect the main supply with the micro-USB detached, and **confirm it booted**:

```sh
nprflash console --send version
```

This talks to the application's UART console on Connector 1 (921600 8N1) via a
Raspberry Pi Debug Probe or any 3.3 V USB-TTL adapter. It does **not** work
over the micro-USB port, which only speaks to the bootloader. If you still have
the micro-USB attached, unplug it; the console is a physically separate
interface.

An accepted flash is not the same as a booting one. The bootloader reports
success once it has stored an image; whether that image runs is a separate
question, and the console banner is the only thing that answers it.

## Flashing over Ethernet

If the modem's firmware provides the upload commands, an image can be installed
across the network with no physical access at all:

```sh
nprflash netflash firmware.nfw --host 192.168.0.253
```

**This needs firmware that supports it**: `fwbegin`, `fwd`, `fwend` and
`slots`. Stock firmware has none of them, and `netflash` will say so and stop.
Use the USB bootloader path above for those.

Run it from a host on the modem's own LAN segment; its management address is
not reachable from anywhere else.

If the modem's HMI is password-protected, pass `--password`. The passphrase is
not sent: the modem issues a random challenge and the tool answers with
`SHA-256(challenge || SHA-256(passphrase))`, so a listener learns nothing
reusable and the exchange differs on every connection.

The image goes into the modem's spare slot, never over the one it is running.
The modem then recomputes the CRC over what actually landed in flash and
refuses the update if it disagrees, so a truncated or corrupted transfer is
rejected before anything is committed. Only then do the boot flags move, and
the slots are swapped on the next reset:

```sh
nprflash netflash firmware.nfw --host 192.168.0.253 --reboot
```

Two properties worth relying on. If the new image fails to validate, the modem
keeps booting the old one; a failed update leaves the unit exactly as it was.
And the image being replaced stays in the spare slot afterwards, so the previous
firmware is still on the device.

Expect roughly 12 kB/s, so about ten seconds for a typical image, plus a couple
of seconds of erase during which the modem stops responding.

## Packaging firmware

Two things are easy to conflate, and the distinction matters:

- a **firmware image** is the raw binary that runs on the MCU (`firmware.bin`);
- a **container** is the `.nfw` file wrapping that image with the metadata the
  bootloader checks.

`build` turns the first into the second:

```sh
nprflash build -v 26072208 -w 240719 -o firmware.nfw firmware.bin
nprflash info firmware.nfw
```

`-v` is the firmware version (`YYMMDDRR`) and `-w` the hardware ID the image is
built for. The device rejects a container whose hardware ID does not match its
own.

Note the container's version is metadata the bootloader validates; it is not
the version the running firmware reports, which is compiled into the image.
Setting them independently is a good way to confuse yourself later.

Give every build a distinct version. It is the only runtime evidence of which
image is running, and an unchanged version cannot distinguish a successful
reflash from one that silently did nothing.

## Console

The application's console is USART2 (PD5/PD6) on rear Connector 1, at
**921600 8N1**. Any 3.3 V USB-TTL adapter works, as does the UART side of a
Raspberry Pi Debug Probe. Do not connect an RS-232 level adapter directly.

```sh
nprflash console                                  # watch, until Ctrl-C
nprflash console --send version                   # ask, print, exit
nprflash console --send version --send "display config"
```

With `--send` it finishes once the reply goes quiet, so there is no duration to
guess. `--seconds` imposes a hard limit if you want one.

Three behaviours are worth knowing, all handled automatically:

- The firmware polls its UART one byte per main-loop pass with no buffering, so
  burst-written input is mostly dropped. Sends are paced per character.
- Input accumulates until a carriage return arrives; a send whose CR is lost
  leaves a fragment that silently prefixes the next command. A flushing CR is
  sent first.
- Data arriving while nothing holds the port open is buffered and delivered on
  the next open, so a stale capture can otherwise masquerade as a live one, so the
  input buffer is discarded on connect. For the same reason, a boot banner
  appearing immediately after connecting is not evidence of a fresh boot.

## Recovery

A firmware image that does not boot cannot brick the unit. The bootloader
retries roughly ten times, then waits for USB. Reconnect the micro-USB and flash
a known-good `.nfw`.

Note that the device only verifies an image's checksum **after** writing every
block, so a corrupt image is stored before it is rejected and must be reflashed.
`nprflash` checks the container's CRC locally first, so a corrupt file is
refused before it reaches the device.

## Tests

```sh
pip install -e ".[dev]"
pytest -q
```

`tests/fixtures/frames.json` holds reference frames for the bootloader wire
format. They pin compatibility: a change that breaks them breaks compatibility
with the device. No hardware is required to run the suite.

## Licence

GPL-3.0-or-later. See [LICENSE](LICENSE).

---

Not affiliated with or endorsed by the manufacturer.
