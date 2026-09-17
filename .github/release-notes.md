Firmware tooling for the **NPR-H 3.0** packet radio modem: build `.nfw` containers, flash them over the bootloader's USB serial port or across the network, and read the runtime console to confirm what the unit is actually running.

**Debian / Ubuntu / Raspberry Pi OS** - install it from the packet-net apt repository, and `apt upgrade` keeps it current:
```
curl -fsSL https://packet-net.github.io/apt/pubkey.asc | sudo gpg --dearmor -o /usr/share/keyrings/packet-net.gpg
echo "deb [signed-by=/usr/share/keyrings/packet-net.gpg] https://packet-net.github.io/apt ./" | sudo tee /etc/apt/sources.list.d/packet-net.list
sudo apt update && sudo apt install nprflash
```
The `.deb` asset below is the same package, for installing by hand: `sudo apt install ./nprflash___VER___all.deb`.

One package serves `amd64`, `arm64` and `armhf`: nprflash is pure Python and runs on the Python already on the machine, so there is nothing architecture-specific to build and pyserial comes from the distribution as `python3-serial`. It needs **Python 3.10 or newer**, so Debian 12 (bookworm), Ubuntu 22.04, Raspberry Pi OS bookworm and later. On Debian 11 (bullseye) and Ubuntu 20.04 apt will decline it and name the Python it found; use pip there.

**Anywhere else** - macOS, Windows, or a Linux machine without the apt repository:
```
pip install git+https://github.com/__REPO__@v__VER__
nprflash --help
```
`pipx install git+https://github.com/__REPO__@v__VER__` does the same into its own environment, which is what a recent distribution's Python will insist on. From a clone, `pip install .` in the checkout.

`SHA256SUMS` covers the `.deb` - verify with `sha256sum -c SHA256SUMS`.

On Linux you need permission for the serial port, which is usually membership of `dialout` (`uucp` on Arch): `sudo usermod -aG dialout "$USER"`, then log out and back in.

Reminder of the two power states, because they decide which interface exists: the unit runs its bootloader and offers the USB serial port only while powered from the micro-USB with the main supply disconnected, and runs the application, with the Connector 1 console and nothing on USB, only on the main supply. A silent console or a missing serial port is usually that, not a fault.
