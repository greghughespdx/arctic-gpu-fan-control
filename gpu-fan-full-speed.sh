#!/usr/bin/env bash
# Set every ARCTIC controller channel to full speed. Used as the fan-control
# service's ExecStopPost, and safe to run by hand at any time.
set -uo pipefail
H=""
for h in /sys/class/hwmon/hwmon*; do
  [[ "$(cat "$h/name" 2>/dev/null)" == arctic_fan ]] && H="$h"
done
if [[ -z "$H" ]]; then
  logger -t gpu-fan-full-speed "arctic_fan hwmon not present; cannot set full speed"
  exit 0
fi
for i in $(seq 1 10); do
  echo 255 > "$H/pwm$i" 2>/dev/null || true
done
logger -t gpu-fan-full-speed "all channels set to PWM 255"
