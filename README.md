# nprflash

Firmware tooling for the **NPR-H 3.0** packet radio modem. Builds `.nfw`
containers, flashes them over the bootloader's USB serial port, and reads the
runtime console so you can confirm what the unit is actually running.

Runs natively on Linux, macOS and Windows.

## Install

```sh
pip install -e .
```

Requires Python 3.10+. Dependencies: `cbor2`, `pyserial`.

On Linux you need permission for the serial port — usually membership of
`dialout` (or `uucp` on Arch):

```sh
sudo usermod -aG dialout "$USER"    # log out and back in
```

## Power states matter

The unit exposes exactly one interface at a time, and which one depends on how
it is powered:

| Powered by | What runs | What you get |
|---|---|---|
| micro-USB, main supply **disconnected** | bootloader | USB serial port `0483:5740` — `probe`, `flash` |
| main supply, micro-USB **disconnected** | application | USART2 console on Connector 1 — `console` |

So a silent console is normal while USB-powered, and a missing serial port is
normal while running on the main supply. Check which supply is connected before
concluding anything is broken.

## Flashing

Put the unit in its bootloader — power from the micro-USB with the main supply
disconnected — then:

```sh
nprflash probe                       # identify: hardware ID, bootloader version
nprflash flash firmware.nfw
```

Reconnect the main supply with the micro-USB detached, and **confirm it booted**:

```sh
nprflash console --send version --seconds 20
```

An accepted flash is not the same as a booting one. The bootloader reports
success once it has stored an image; whether that image runs is a separate
question, and the console banner is the only thing that answers it.

## Packaging firmware

```sh
nprflash build -v 26072208 -w 240719 -o firmware.nfw firmware.bin
nprflash info firmware.nfw
```

`-v` is the firmware version (`YYMMDDRR`) and `-w` the hardware ID the image is
built for. The device rejects an image whose hardware ID does not match its own.

Give every build a distinct version. It is the only runtime evidence of which
image is running, and an unchanged version cannot distinguish a successful
reflash from one that silently did nothing.

## Console

The application's console is USART2 (PD5/PD6) on rear Connector 1, at
**921600 8N1**. Any 3.3 V USB-TTL adapter works, as does the UART side of a
Raspberry Pi Debug Probe. Do not connect an RS-232 level adapter directly.

```sh
nprflash console --seconds 60                          # catch a boot banner
nprflash console --send version --send "display config" --seconds 20
```

Three behaviours are worth knowing, all handled automatically:

- The firmware polls its UART one byte per main-loop pass with no buffering, so
  burst-written input is mostly dropped. Sends are paced per character.
- Input accumulates until a carriage return arrives; a send whose CR is lost
  leaves a fragment that silently prefixes the next command. A flushing CR is
  sent first.
- Opening the port resets the unit, so commands wait for the `ready>` prompt.
  **Batch work into one invocation** — `--send` is repeatable. The bootloader
  only retries an application about ten times before falling back to waiting for
  USB, and a loop of one-shot commands can exhaust that.

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
