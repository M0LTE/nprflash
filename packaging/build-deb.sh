#!/usr/bin/env bash
# Builds the nprflash .deb.
#
#   packaging/build-deb.sh <version> [outdir]
#
# Produces <outdir>/nprflash_<version>_all.deb. Default outdir is <repo>/artifacts.
#
# There is no architecture argument, unlike the sibling tait-codeplug script. nprflash is pure
# Python running on the distro's own interpreter, so nothing here is compiled and no runtime is
# bundled: one Architecture: all package is the same correct package on amd64, arm64 and armhf,
# and it is tens of kB rather than tens of MB. What that buys in size it spends in dependencies,
# which is what the Depends line below is for.
#
# No systemd unit and no configuration. This is a CLI tool that opens a serial port when you run
# it: no daemon, no user to create, no config to seed. The two maintainer scripts it does carry
# are about Python bytecode and nothing else; see the comment on them below.
set -euo pipefail

VERSION="${1:?usage: build-deb.sh <version> [outdir]}"

# A Debian version must start with a digit. Note for later, if a prerelease scheme is ever
# wanted: 1.0.0~rc1 sorts BEFORE 1.0.0, whereas 1.0.0-rc1 sorts after it, so a tag like
# v1.0.0-rc1 would make apt treat the candidate as newer than the eventual release.
case "$VERSION" in
  [0-9]*) ;;
  *) echo "version '$VERSION' does not start with a digit, which Debian requires" >&2; exit 2 ;;
esac

# Argument 2 is the output directory, not an architecture. tait-codeplug's script is
# <version> <arch> <outdir> and is called in a per-arch loop, so anyone carrying that habit
# across would quietly get a directory named `amd64` with the package inside it and a release
# step that then finds nothing to upload. Refusing costs one case statement; diagnosing the
# empty upload costs an afternoon.
case "${2:-}" in
  amd64|arm64|armhf|i386|all|linux-*)
    echo "'$2' looks like an architecture, but this package is Architecture: all - argument 2 is the output directory" >&2
    exit 2 ;;
esac

# dpkg-deb ships in the Essential `dpkg` package, so this only trips on a non-Debian host.
command -v dpkg-deb >/dev/null || { echo "dpkg-deb not found - this needs a Debian-family host" >&2; exit 3; }

# Directories inherit the caller's umask, and a developer box set to 002 produces
# group-writable 0775 directories inside the package, which is not what a .deb should ship
# (lintian: non-standard-dir-perm). Pin it so the package is the same from any shell.
umask 022

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
OUTDIR="${2:-$ROOT/artifacts}"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

DOCDIR=/usr/share/doc/nprflash
# The Debian location for a system-installed Python package. Not site-packages, which on Debian
# is where a distutils build from source would land and is not on the system interpreter's path,
# and not a private directory under /usr/share with a sys.path fiddle in the launcher, because
# `import nprflash` from a script or an interactive python3 is a reasonable thing to want.
SITEDIR=/usr/lib/python3/dist-packages

# The version the release is cut at has to be the version the code reports, or `apt policy
# nprflash` and `python3 -c 'import nprflash; print(nprflash.__version__)'` disagree on the same
# machine with no way to tell which is lying. Two files declare it, and neither is generated from
# the other, so both get checked. This lives here rather than in the workflow so that a build run
# by hand fails the same way CI does, and so there is one copy of the rule to keep true.
PYPROJECT_VERSION="$(sed -n 's/^version = "\(.*\)"$/\1/p' "$ROOT/pyproject.toml" | head -1)"
INIT_VERSION="$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' "$ROOT/nprflash/__init__.py" | head -1)"
if [ "$VERSION" != "$PYPROJECT_VERSION" ] || [ "$VERSION" != "$INIT_VERSION" ]; then
  echo "version mismatch: building $VERSION, but pyproject.toml says '$PYPROJECT_VERSION' and nprflash/__init__.py says '$INIT_VERSION'" >&2
  echo "bump all three, or tag the version the code already declares" >&2
  exit 2
fi

mkdir -p "$STAGE/root/usr/bin" \
         "$STAGE/root$SITEDIR" \
         "$STAGE/root$DOCDIR" \
         "$STAGE/root/DEBIAN"

# Copy the package tree as source, file by file. Only *.py, and __pycache__ is excluded on the
# way in rather than deleted afterwards: a stray .pyc compiled by the build machine's interpreter
# is useless to a target running a different Python and lintian rejects it outright
# (package-installs-python-bytecode). Debian's own policy is that bytecode is the installing
# machine's business; dpkg does not compile it either, so the first run pays a few milliseconds
# and writes nothing, because /usr/lib/python3/dist-packages is not writable by the user.
MODULE_COUNT=0
while IFS= read -r rel; do
  install -D -m 0644 "$ROOT/$rel" "$STAGE/root$SITEDIR/$rel"
  MODULE_COUNT=$((MODULE_COUNT + 1))
done < <(cd "$ROOT" && find nprflash -type f -name '*.py' -not -path '*/__pycache__/*' | sort)
[ "$MODULE_COUNT" -gt 0 ] || { echo "found no .py files under $ROOT/nprflash - wrong directory?" >&2; exit 4; }

# Anything in the package tree that is not Python would be package data that setuptools ships and
# this script would silently drop, which is the kind of bug that only shows up as a traceback on
# someone else's machine. Fail at build time instead, where the fix is obvious: copy it too.
STRAYS="$(cd "$ROOT" && find nprflash -type f -not -name '*.py' -not -path '*/__pycache__/*' | sort)"
if [ -n "$STRAYS" ]; then
  echo "non-Python files in the package tree that this script does not install:" >&2
  echo "$STRAYS" >&2
  echo "add them to the copy above (and to setuptools package-data) before releasing" >&2
  exit 4
fi

# The launcher, written here rather than taken from `pip install`. setuptools generates a console
# script with the build machine's interpreter path baked into the shebang, which on a CI runner is
# something like /opt/hostedtoolcache/Python/3.12.x/x64/bin/python: correct there, absent on every
# machine that installs the package. /usr/bin/python3 is the system interpreter the Depends line
# is about, so that is what the shebang names.
#
# Worth knowing: /usr/local/lib/python3/dist-packages comes before /usr/lib/... on sys.path, so a
# `sudo pip install nprflash` on the same box shadows the packaged copy and this launcher will run
# it. That is normal Debian behaviour for any Python package, not something this script can fix,
# but it is the first thing to check when a packaged version and the reported behaviour disagree.
cat > "$STAGE/root/usr/bin/nprflash" <<'LAUNCHER'
#!/usr/bin/python3
"""Debian launcher for nprflash, installed as /usr/bin/nprflash."""

import sys

from nprflash.cli import main

sys.exit(main())
LAUNCHER
chmod 0755 "$STAGE/root/usr/bin/nprflash"

# Two maintainer scripts, which a CLI tool would not otherwise need. They exist because of
# bytecode, and nothing else. Python writes __pycache__/*.pyc beside the source the first time a
# module is imported by a user who can write there, which for /usr/lib/python3/dist-packages means
# anyone running the tool under sudo. dpkg knows nothing about those files, so on removal it finds
# the directory it owns is not empty, warns, and leaves the tree behind for ever. Measured, not
# assumed: one `sudo nprflash --help` in a bookworm container is enough to produce
# "dpkg: warning: while removing nprflash, directory ... not empty so not removed".
#
# py3compile and py3clean are what dh_python3 generates for exactly this, and they live in
# python3-minimal, which the python3 dependency above pulls in, so they are always present. The
# `command -v` guard is the same belt-and-braces dh_python3 writes. Compiling at install time is
# also the only correct time to do it: the .pyc has to match the interpreter that will import it,
# which the build machine does not know.
#
# py3compile is deliberately not `|| true`. The only realistic way it fails is source that the
# installed interpreter cannot compile, and that would be a SyntaxError on first run anyway.
# Better to fail the install, in front of the operator, than at the moment a modem is sitting in
# its bootloader waiting to be flashed.
cat > "$STAGE/root/DEBIAN/postinst" <<'POSTINST'
#!/bin/sh
set -e

case "$1" in
  configure)
    if command -v py3compile >/dev/null 2>&1; then
      py3compile -p nprflash
    fi
    ;;
esac
POSTINST
chmod 0755 "$STAGE/root/DEBIAN/postinst"

cat > "$STAGE/root/DEBIAN/prerm" <<'PRERM'
#!/bin/sh
set -e

case "$1" in
  remove|upgrade|deconfigure)
    if command -v py3clean >/dev/null 2>&1; then
      py3clean -p nprflash
    fi
    ;;
esac
PRERM
chmod 0755 "$STAGE/root/DEBIAN/prerm"

install -m 0644 "$HERE/copyright" "$STAGE/root$DOCDIR/copyright"

# Debian changelog. A numeric SOURCE_DATE_EPOCH keeps rebuilds of a tag byte-identical;
# anything else (an ISO string from a CI event payload, say) falls back to now rather than
# failing the build on `date -R`.
case "${SOURCE_DATE_EPOCH:-}" in
  ''|*[!0-9]*) CHANGELOG_DATE="$(date -R)" ;;
  *)           CHANGELOG_DATE="$(date -R --date="@$SOURCE_DATE_EPOCH")" ;;
esac
cat > "$STAGE/changelog.Debian" <<EOF
nprflash ($VERSION) unstable; urgency=medium

  * Release $VERSION. Notes:
    https://github.com/M0LTE/nprflash/releases/tag/v$VERSION

 -- Tom Fanning M0LTE <tom@m0lte.uk>  $CHANGELOG_DATE
EOF
gzip -9n -c "$STAGE/changelog.Debian" > "$STAGE/root$DOCDIR/changelog.Debian.gz"
chmod 0644 "$STAGE/root$DOCDIR/changelog.Debian.gz"

INSTALLED_SIZE="$(du -k -s --exclude=DEBIAN "$STAGE/root" | cut -f1)"

# Depends, both of them from the distro:
#
#  python3 (>= 3.10) - the code uses `X | None` annotations under `from __future__ import
#    annotations` and structural pattern matching is available to it, and pyproject.toml already
#    declares requires-python >= 3.10. Debian's python3 package version tracks the default
#    interpreter, so this is the honest way to say it. It does exclude bullseye and Ubuntu 20.04,
#    which are on 3.9: there apt will refuse the package rather than install something that
#    crashes on import, which is the right failure.
#  python3-serial - Debian's pyserial (3.5 in bookworm, trixie and Ubuntu 24.04, so the
#    pyserial>=3.5 floor in pyproject.toml is met without a version qualifier).
#
# Architecture: all because there is nothing architecture-specific in the payload. python3-serial
# is pure Python too, so no arch-specific extension arrives through the dependency either.
cat > "$STAGE/root/DEBIAN/control" <<EOF
Package: nprflash
Version: $VERSION
Architecture: all
Maintainer: Tom Fanning M0LTE <tom@m0lte.uk>
Installed-Size: $INSTALLED_SIZE
Depends: python3 (>= 3.10), python3-serial
Section: hamradio
Priority: optional
Homepage: https://github.com/M0LTE/nprflash
Description: firmware tooling for the NPR-H 3.0 packet radio modem
 Build .nfw firmware containers, flash them to an NPR-H 3.0 over the
 bootloader's USB serial port or across the network to a running unit, and read
 the application's serial console to confirm what the modem is actually
 running.
 .
 This package uses the Python already on the system rather than bundling an
 interpreter, so it is architecture-independent and installs the same on a
 Raspberry Pi as on a server.
 .
 GPL-3.0-or-later.
EOF

# md5sums, which `dpkg-deb --build` does not generate on its own; in a normal Debian build
# dh_md5sums does it. Three lines, and it is what lets `dpkg -V nprflash` and debsums say whether
# an installed file has been changed since it arrived. On this package that is the quick answer to
# "am I actually running the packaged copy?" on a machine that has also seen a pip install.
( cd "$STAGE/root" && find . -type f -not -path './DEBIAN/*' -printf '%P\0' | sort -z \
    | xargs -0 md5sum > DEBIAN/md5sums )
chmod 0644 "$STAGE/root/DEBIAN/md5sums"

mkdir -p "$OUTDIR"
DEB="$OUTDIR/nprflash_${VERSION}_all.deb"
# -Zxz, not the host dpkg's default. A recent dpkg (and Ubuntu's, for years) builds
# control.tar.zst/data.tar.zst, and dpkg only learned to read zstd in 1.21.18: bullseye ships
# 1.20.14, which refuses the archive outright with "unknown compression for member
# control.tar.zst" before it gets as far as the dependencies. That matters even though the
# Depends line above already rules bullseye out on Python 3.9, because the two failures read
# completely differently: a dependency refusal names the missing version and tells the operator
# what to do, whereas an unreadable archive looks like a corrupt download. Same reasoning for any
# older dpkg that turns up in front of this package, and the payload is a few tens of kB, so the
# choice of compressor costs nothing either way.
dpkg-deb -Zxz --build --root-owner-group "$STAGE/root" "$DEB"
echo "built $DEB"
