#!/usr/bin/env bash
# fan-log.sh <stage> [interval_seconds] [channel ...]
# Appends one CSV line per interval (default 5 s) with the RPM and PWM readback
# of the named ARCTIC controller channels (default: 1 2).
# Columns: timestamp,rpm<n>,pwm<n>,... in the order the channels were given.
#
# Used for soak runs: start it before a load test, stop it after, then plot or
# eyeball the CSV to see whether the fans hunted.
set -uo pipefail

STAGE="${1:?usage: fan-log.sh <stage> [interval] [channel ...]}"
INT="${2:-5}"
shift 2 2>/dev/null || shift $#
CHANNELS=("$@")
[[ ${#CHANNELS[@]} -gt 0 ]] || CHANNELS=(1 2)

OUT="/var/tmp/fan-${STAGE}.csv"

H=""
for h in /sys/class/hwmon/hwmon*; do
  [[ "$(cat "$h/name" 2>/dev/null)" == arctic_fan ]] && H="$h"
done
[[ -n "$H" ]] || { echo "no arctic_fan hwmon" >&2; exit 1; }

if [[ ! -s "$OUT" ]]; then
  hdr="timestamp"
  for c in "${CHANNELS[@]}"; do hdr="$hdr,rpm$c,pwm$c"; done
  echo "$hdr" > "$OUT"
fi

while true; do
  line="$(date -Is)"
  for c in "${CHANNELS[@]}"; do
    line="$line,$(cat "$H/fan${c}_input" 2>/dev/null),$(cat "$H/pwm${c}" 2>/dev/null)"
  done
  echo "$line" >> "$OUT"
  sleep "$INT"
done
