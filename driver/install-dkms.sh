#!/usr/bin/env bash
# Build and install the arctic_fan_controller hwmon driver with DKMS.
#
# Only needed on kernels that do not already ship the driver. Check first:
#   modinfo arctic_fan_controller
# If that prints a module path, the kernel already has it and you can skip this.
#
# Run as root from the driver directory:  sudo ./install-dkms.sh
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "run as root: sudo ./install-dkms.sh" >&2
  exit 1
fi

NAME=arctic-fan-controller
VER=1.0
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="/usr/src/${NAME}-${VER}"

command -v dkms >/dev/null || {
  echo "dkms is not installed (apt install dkms, dnf install dkms)" >&2
  exit 1
}

if dkms status "${NAME}/${VER}" | grep -q .; then
  echo "removing existing ${NAME}/${VER} from dkms"
  dkms remove -m "$NAME" -v "$VER" --all || true
fi

rm -rf "$DEST"
mkdir -p "$DEST"
install -m 0644 "$SRC/arctic_fan_controller.c" "$DEST/arctic_fan_controller.c"
install -m 0644 "$SRC/Makefile"                "$DEST/Makefile"
install -m 0644 "$SRC/dkms.conf"               "$DEST/dkms.conf"

dkms add -m "$NAME" -v "$VER"
dkms build -m "$NAME" -v "$VER"
dkms install -m "$NAME" -v "$VER"

modprobe arctic_fan_controller || true

echo
echo "installed. The controller should now appear as an hwmon device named"
echo "arctic_fan. Check with:"
echo "  for h in /sys/class/hwmon/hwmon*; do echo \"\$h \$(cat \$h/name)\"; done"
