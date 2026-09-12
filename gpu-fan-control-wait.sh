#!/usr/bin/env bash
# Block until every GPU named in the config and the ARCTIC controller hwmon
# exist. Gives up after 120 s so the unit fails loudly rather than hanging
# forever; ExecStopPost then puts the fans to full speed.
#
# The card list comes from the pci_id lines in the config file, so this script
# never needs editing when the config changes.
set -uo pipefail

CONF="${GPU_FAN_CONTROL_CONF:-/etc/gpu-fan-control.conf}"
if [[ ! -r "$CONF" ]]; then
  echo "cannot read $CONF" >&2
  exit 1
fi

CARDS=$(sed -n 's/^[[:space:]]*pci_id[[:space:]]*=[[:space:]]*//p' "$CONF" | tr -d '\r')
if [[ -z "$CARDS" ]]; then
  echo "no pci_id entries in $CONF" >&2
  exit 1
fi

for _ in $(seq 1 120); do
  ok=1
  for c in $CARDS; do
    compgen -G "/sys/bus/pci/devices/${c}/hwmon/hwmon*/temp1_input" >/dev/null || ok=0
  done
  found=0
  for h in /sys/class/hwmon/hwmon*; do
    [[ "$(cat "$h/name" 2>/dev/null)" == arctic_fan ]] && found=1
  done
  [[ $ok -eq 1 && $found -eq 1 ]] && exit 0
  sleep 1
done

echo "timed out waiting for GPU hwmon and arctic_fan hwmon" >&2
exit 1
