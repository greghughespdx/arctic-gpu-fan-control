# arctic-gpu-fan-control

Uses an ARCTIC fan controller to drive blowers/fans based on GPU temperatures on 
Linux. Built for passive server GPUs that have no fan of their own and sit in a 
chassis where the BIOS fan controls cannot effectively meet the cooling needs.

The loop reads each card's edge and junction temperature once per second, maps
that to a fan speed through a definable curve, and writes the speed to one
channel of an ARCTIC Fan Controller. It smooths the measured temperatures, 
anticipates where temperature is heading, limits how fast fan speed may change, 
and goes to full speed on any error.

Includes an option to send fan data to a [GPU-Hot](https://github.com/psalias2006/gpu-hot) dashboard (see below).

## Driver requirement (note: read this first)

The fan controller needs a kernel driver, `arctic_fan_controller`, which
exposes the device as an hwmon named `arctic_fan`. Without it nothing in this
repository works.

Checked against the mainline Linux git tree at the time of publication (September 
2026): the driver is present from tag `v7.2-rc1` onward and absent in `v7.1` and 
earlier. Linux 7.2 is therefore the first released kernel that ships it. Check 
your own kernel with:

```bash
modinfo arctic_fan_controller
```

If that prints nothing, build the DKMS package in `driver/`. It is the same
upstream source with one version-conditional include, so it compiles on
older kernels. Provenance, license and exact evidence are at `driver/README.md`.

## The hardware

The [ARCTIC ACFAN00351A Fan Controller](https://www.arctic.de/us/Fan-Controller/ACFAN00351A) was used in the development of this repo. 
It connects by USB, either to an internal USB header on the mainboard or to any 
USB port, and drives up to 10 fans. Linux sees it as a USB HID device and, with 
the driver above, as an hwmon device named `arctic_fan` with `pwm1` through 
`pwm10` (0 to 255, writable) and `fan1_input` through `fan10_input` (RPM, 
read-only).

Fan speed via this device is manual only. It never changes speed on its own; it
does exactly what the host last told it. That is why something has to drive the
loop, and why the fail-safe behavior described below matters.

## How the control loop works

The PWM for each channel is the sum of three parts, which is then limited:

- **Curve.** A piecewise-linear table of control temperature to PWM, flat
  outside the end points. Smooth across the whole range, so there is no cliff
  between quiet and loud fan noise.
- **Derivative.** A fast temperature rise raises fan speed before the card is
  hot; a fast temperature fall allows easing down without waiting. The slope is a
  least-squares fit over the last N samples, so sensor noise does not chatter
  the fans.
- **Integral.** Steady-state trim. Off by default. Turn it on only if a soak
  run shows a card settling above target using the curve alone.

The control temperature is `max(edge, junction - junction_offset)`, whichever
sensor is closer to its own limit, then low-pass filtered. Why: an inference 
load makes these cards saw-tooth several degrees every ten to fifteen seconds 
as the job pauses between iterations, and without the filter both the curve 
and the derivative chase that sawtooth, so the fans may pump audibly. A real 
load step is sustained, so it survives the filter.

The result is then rate limited, separately up and down, and held inside a dead
band. As a result, speed changes are gradual in both directions.

## Mapping a GPU to a fan channel

There is no way to ask the controller which fan is plugged into which channel,
and no way to ask the chassis which fan blows on which card. You have to 
determine this (or ask your AI agent to figure it out).

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
airflow during the test. Watch the temperatures while you do it, and abort to
full speed if any component approaches its limit.

Record the fan/GPU pairs in the config as `channel` and `pci_id`.

## Safety behavior

The fan values are set to full speed (PWM 255) upon any of these conditions:

- any read error on a GPU sensor or on the controller
- the `arctic_fan` hwmon disappearing, for example if the USB device is unplugged
- a PWM write that fails after its retries
- any unhandled exception
- SIGTERM or SIGINT
- normal exit

The service also goes to full speed when it stops for any reason, because
`ExecStopPost` runs `gpu-fan-full-speed.sh`. `StartLimitIntervalSec=0` ensures
systemd never gives up restarting it, since every failed attempt puts the fans
back to full speed. Underneath all of this, set the BIOS fan profile to full
speed as the last resort for the case where this service never runs at all.

Also: `gpu-fan-full-speed.sh` is safe to run by hand at any time.

## Install

```bash
sudo ./install.sh
sudo nano /etc/gpu-fan-control.conf     # replace the placeholder values
sudo systemctl start gpu-fan-control
```

The installer copies the scripts to `/usr/local/bin`, installs the systemd unit
and enables it, and writes the example config to `/etc/gpu-fan-control.conf` if
no config exists there yet. It does not start the service, because the shipped
config has placeholder PCI addresses in it that must be replaced with the actual
PCI addresses of your devices.

If the required driver is not in your kernel (see the "read this first" section 
above), install it first:

```bash
cd driver && sudo ./install-dkms.sh
```

## Configuration

All configuration settings are in `/etc/gpu-fan-control.conf`. No code changes are 
necessary to tune the system. The `gpu-fan-control.conf.example` file documents 
every config key: loop timing and logging, curve, PWM floor and ceiling, smoothing 
time constant, derivative gain and window, optional integral term, slew limits and
dead band, and one `[channel:NAME]` section per fan, specifying its controller 
channel number and the PCI address of the GPU it cools.

Values in `[defaults]` apply to every channel, and any value can be overridden
inside a single channel section, for example to assign one card a different curve.

Restart the service after an edit: `sudo systemctl restart gpu-fan-control`.

Important: Anchor the curve on your own measurements rather than the example 
values. Run the cards at their normal sustained load, hold a fixed PWM, wait 
for the temperature to settle, write down the PWM/temp values, and repeat at two 
or three speeds to determine proper values and ensure adequate cooling.

## Reading the log

The program loop logs to the journal:

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

Lines beginning `FAIL-SAFE` mean the fans were forced to full speed and describe 
why.

For a soak run, `tools/fan-log.sh` writes a CSV of RPM and PWM readings at a
fixed interval, which makes it easy to see afterwards whether the fans hunted:
`fan-log.sh mystage 5 1 2`.

## Showing the same fans in gpu-hot

[gpu-hot](https://github.com/psalias2006/gpu-hot) is a web based dashboard that can display these fans on its GPU cards (pending an upstream pull request; until it merges the feature is in [this fork](https://github.com/greghughespdx/gpu-hot)) 
through its `EXTERNAL_FANS` setting, which maps a GPU PCI address to an hwmon 
fan channel. Point it at the same device and the same channel numbers used here, 
for example: `{"0000:00:00.0": {"source": "hwmon", "name": "arctic_fan", "channel": 1}}`,
and a card that has no fan of its own shows the speed and RPM of the blower
that's cooling it. Note that gpu-hot reads the same sysfs files this loop writes, 
so the number it shows is the actual speed this program loop set.

## License

MIT for the tool, copyright 2026 Greg Hughes. See `LICENSE`.

The kernel driver under `driver/` is GPL-2.0-or-later and keeps its own
copyright and SPDX header. See `driver/README.md`.
