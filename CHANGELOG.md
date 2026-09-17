# Changelog

What changed in each release. The section for a version is lifted into that version's GitHub release notes by `.github/workflows/publish.yml`, so this file is the source of truth for what a release says it did.

Newest first. Add a section before tagging.

## 1.0.0 - 2026-09-17

- **First release, and the first version you can install rather than clone.** `sudo apt install nprflash` on Debian, Ubuntu and Raspberry Pi OS via the [packet-net apt repository](https://github.com/packet-net/apt), after the three lines in the README that add it, and `apt upgrade` then keeps the tool current along with everything else on the machine. The `.deb` is attached to the release as well, for installing one by hand.
- **One package for every machine.** nprflash is pure Python and runs on the system interpreter, so a single 21 kB `Architecture: all` package covers amd64, arm64 and armhf, and pyserial arrives as the distribution's own `python3-serial` rather than being bundled. It needs Python 3.10 or newer, which means Debian 12, Ubuntu 22.04, Raspberry Pi OS bookworm, or anything later. On Debian 11 and Ubuntu 20.04 apt declines it and names the Python it found, instead of installing something that would fail on first import; install with `pip` there.
- **Flash an NPR-H 3.0 over its USB bootloader.** `nprflash probe` identifies the attached unit, `nprflash flash firmware.nfw` writes an image to it, and `nprflash build` wraps a raw `.bin` as the `.nfw` container the bootloader checks. The container's CRC is verified locally first, so a corrupt file is refused before it ever reaches the device.
- **Flash across the network, with no physical access**, on firmware that supports it: `nprflash netflash firmware.nfw --host 192.168.0.253`. The image goes into the spare slot and is CRC-checked in place before the boot flags move, so a failed update leaves the unit running exactly what it was running before, and the previous firmware stays on the device.
- **Read the runtime console** on Connector 1 at 921600 8N1 with `nprflash console --send version`, which is the only thing that answers the question a successful flash does not: whether the image actually boots.
