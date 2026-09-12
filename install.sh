#!/usr/bin/env bash
# Install gpu-fan-control: copy the scripts into /usr/local/bin, install the
# systemd unit, and place an example config if none exists yet.
#
# Run as root from the repository root:  sudo ./install.sh
#
# The service is enabled but NOT started, because the shipped config contains
# placeholder PCI addresses. Edit /etc/gpu-fan-control.conf first, then:
#   systemctl start gpu-fan-control
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "run as root: sudo ./install.sh" >&2
  exit 1
fi

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN=/usr/local/bin
CONF=/etc/gpu-fan-control.conf

install -m 0755 "$SRC/gpu-fan-control.py"       "$BIN/gpu-fan-control.py"
install -m 0755 "$SRC/gpu-fan-control-wait.sh"  "$BIN/gpu-fan-control-wait.sh"
install -m 0755 "$SRC/gpu-fan-full-speed.sh"    "$BIN/gpu-fan-full-speed.sh"
install -m 0755 "$SRC/tools/fan-log.sh"         "$BIN/fan-log.sh"

if [[ -e "$CONF" ]]; then
  echo "keeping existing $CONF"
  install -m 0644 "$SRC/gpu-fan-control.conf.example" "$CONF.example"
  echo "new example written to $CONF.example"
else
  install -m 0644 "$SRC/gpu-fan-control.conf.example" "$CONF"
  echo "wrote $CONF from the example; EDIT IT before starting the service"
fi

install -m 0644 "$SRC/systemd/gpu-fan-control.service" \
  /etc/systemd/system/gpu-fan-control.service

systemctl daemon-reload
systemctl enable gpu-fan-control.service

echo
echo "installed. Next:"
echo "  1. edit $CONF (channel numbers and PCI addresses)"
echo "  2. systemctl start gpu-fan-control"
echo "  3. journalctl -u gpu-fan-control -f"
