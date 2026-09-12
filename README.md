# arctic-gpu-fan-control

Drives ARCTIC blower fans from GPU temperature on Linux. Built for passive
server GPUs that have no fan of their own and sit in a chassis where the BIOS
only knows how to run the case fans flat out.

The loop reads each card's edge and junction temperature once a second, maps
that to a fan speed through a curve you define, and writes the speed to one
channel of an ARCTIC Fan Controller. It smooths the temperature, anticipates
where it is heading, limits how fast the speed may change, and goes to full
speed on any error.

## Driver requirement, read this first

The fan controller needs a kernel driver, `arctic_fan_controller`, which
exposes the device as an hwmon named `arctic_fan`. Without it nothing in this
repository works.

Checked against the mainline Linux git tree: the driver is present from tag
`v7.2-rc1` onward and absent in `v7.1` and earlier. Linux 7.2 is therefore the
first released kernel that ships it. Check your own kernel with:

```bash
modinfo arctic_fan_controller
```

If that prints nothing, build the DKMS package in `driver/`. It is the same
upstream source with one version-conditional include so it also compiles on
older kernels. Provenance, license and exact evidence: `driver/README.md`.

## The hardware

The ARCTIC ACFAN00351A Fan Controller. It connects by USB, either to an
internal USB header on the mainboard or to any USB port, and drives up to 10
fans. Linux sees it as a USB HID device and, with the driver above, as an hwmon
device named `arctic_fan` with `pwm1` through `pwm10` (0 to 255, writable) and
`fan1_input` through `fan10_input` (RPM, read-only).

Fan speed on this device is manual only. It never changes speed on its own; it
does exactly what the host last told it. That is why something has to run the
loop, and why the fail-safe behaviour below matters.

## How the control loop works

The PWM for each channel is the sum of three parts, then limited:

- **Curve.** A piecewise-linear table of control temperature to PWM, flat
  outside the end points. Smooth across the whole range, so there is no cliff
  between quiet and loud.
- **Derivative.** A fast temperature rise raises the fans before the card is
  hot; a fast fall lets them ease down without waiting. The slope is a
  least-squares fit over the last N samples, so sensor noise does not chatter
  the fans.
- **Integral.** Steady-state trim. Off by default. Turn it on only if a soak
  run shows a card settling above target with the curve alone.

The control temperature is `max(edge, junction - junction_offset)`, whichever
sensor is closer to its own limit, then low-pass filtered. The filter earns its
place: an inference load makes these cards saw-tooth several degrees every ten
to fifteen seconds as the job pauses between iterations, and without the filter
both the curve and the derivative chase that sawtooth and the fans pump audibly.
A real load step is sustained, so it survives the filter.

The result is then rate limited, separately up and down, and held inside a dead
band, so speed changes are gradual in both directions.

## Mapping a GPU to a fan channel

There is no way to ask the controller which fan is plugged into which channel,
and no way to ask the chassis which fan blows on which card. You measure it.

1. Get the PCI addresses of the cards: `ls -l /sys/class/drm/card*/device`, or
   `lspci -D | grep -i vga`.
2. Find the controller: `grep . /sys/class/hwmon/*/name` and look for
   `arctic_fan`.
3. Put both cards under a steady load and hold every channel at the same
   moderate PWM until the temperatures settle.
4. Raise one channel to full speed and leave the rest alone. Within a couple of
   minutes one card's temperature falls noticeably faster than the other. That
   card is the one this channel cools.
5. Put the channel back to the baseline, let it settle, then repeat for the
   next channel.

Raising a channel rather than lowering one matters: no card is ever starved of
airflow during the test. Watch the temperatures while you do it and abort to
full speed if anything approaches its limit.

Record the pairs in the config as `channel` and `pci_id`.

## Safety behaviour

The fans go to full speed (PWM 255) on all of these:

- any read error on a GPU sensor or on the controller
- the `arctic_fan` hwmon disappearing, for example if the USB device is unplugged
- a PWM write that fails after its retries
- any unhandled exception
- SIGTERM or SIGINT
- normal exit

The service also goes to full speed when it stops for any reason, because
`ExecStopPost` runs `gpu-fan-full-speed.sh`. `StartLimitIntervalSec=0` means
systemd never gives up restarting it, since every failed attempt puts the fans
back to full speed. Underneath all of it, set the BIOS fan profile to full
speed as the last resort for the case where this service never runs at all.

`gpu-fan-full-speed.sh` is safe to run by hand at any time.

## Install

```bash
sudo ./install.sh
sudo nano /etc/gpu-fan-control.conf     # replace the placeholder values
sudo systemctl start gpu-fan-control
```

The installer copies the scripts to `/usr/local/bin`, installs the systemd unit
and enables it, and writes the example config to `/etc/gpu-fan-control.conf` if
no config is there yet. It does not start the service, because the shipped
config has placeholder PCI addresses in it.

If the driver is not in your kernel, install it first:

```bash
cd driver && sudo ./install-dkms.sh
```

## Configuration

Everything lives in `/etc/gpu-fan-control.conf`. Nothing needs a code change to
tune. `gpu-fan-control.conf.example` documents every key: loop timing and
logging, the curve, the PWM floor and ceiling, the smoothing time constant, the
derivative gain and window, the optional integral term, the slew limits and the
dead band, and one `[channel:NAME]` section per fan giving its channel number
and the PCI address of the card it cools.

Values in `[defaults]` apply to every channel, and any of them can be overridden
inside a single channel section, for example to give one card a different curve.

Restart the service after an edit: `sudo systemctl restart gpu-fan-control`.

Anchor the curve on your own measurements rather than the example values. Run
the cards at their normal sustained load, hold a fixed PWM, wait for the
temperature to settle, write down the pair, and repeat at two or three speeds.

## Reading the log

The loop logs to the journal:

```bash
journalctl -u gpu-fan-control -f
```

One line per `log_every` seconds, one entry per channel:

```
card-a edge=61 junc=76 raw=61.0 ctl=59.4 d=+0.21C/s pwm=196
```

- `edge`, `junc` the two raw sensor readings in C
- `raw` the control temperature before smoothing
- `ctl` the smoothed control temperature, which is what the curve actually sees
- `d` the fitted slope in C per second, which is what the derivative term sees
- `pwm` the value written to the controller, 0 to 255

Lines beginning `FAIL-SAFE` mean the fans were forced to full speed and why.

For a soak run, `tools/fan-log.sh` writes a CSV of RPM and PWM readback at a
fixed interval, which makes it easy to see afterwards whether the fans hunted:
`fan-log.sh mystage 5 1 2`.

## Showing the same fans in gpu-hot

gpu-hot can display these fans on its GPU cards through its `EXTERNAL_FANS`
setting, which maps a GPU PCI address to an hwmon fan channel. Point it at the
same device and the same channel numbers used here, for example
`{"0000:00:00.0": {"source": "hwmon", "name": "arctic_fan", "channel": 1}}`,
and the card that has no fan of its own shows the speed and RPM of the blower
actually cooling it. It is read-only and independent of this tool: gpu-hot
reads the same sysfs files this loop writes, so the number it shows is the
speed this loop set.

## License

MIT for the tool, copyright 2026 Greg Hughes. See `LICENSE`.

The kernel driver under `driver/` is GPL-2.0-or-later and keeps its own
copyright and SPDX header. See `driver/README.md`.
