#!/usr/bin/env python3
"""
gpu-fan-control - drive the ARCTIC Fan Controller blowers from amdgpu temperature.

One controller channel per GPU. Each channel's PWM comes from three parts:

  pwm = curve(T) + Kd * dT/dt + Ki * integral(T - target)

  curve(T)  a piecewise-linear table, temperature to PWM. This is the
            proportional mapping and it is smooth across the whole range,
            so there is no cliff between quiet and loud.
  Kd term   anticipation. A fast temperature rise raises the fans before the
            card is hot; a fast fall lets them ease down without waiting.
            dT/dt is a least-squares slope over the last N samples so sensor
            noise does not chatter the fans.
  Ki term   steady-state trim. Off by default (ki = 0); turn it on only if a
            soak shows the card settling above target with the curve alone.

The output is then rate limited (a maximum PWM change per second, separately
up and down) and held inside a dead band, so speed changes are gradual in both
directions.

T is the control temperature: max(edge, junction - junction_offset), whichever
sensor is closer to its own limit, then low-pass filtered with a time constant
of smoothing_tau seconds. The filter matters: an inference benchmark makes the
card's temperature saw-tooth by several degrees every ten to fifteen seconds as
it pauses between iterations, and without the filter both the curve and the
derivative chase that sawtooth and the fans audibly pump. A real load step is
sustained, so it survives the filter and the derivative still sees it.

FAIL-SAFE. The fans go to full speed (PWM 255) on: any read error on the GPU or
the controller, the arctic_fan hwmon disappearing, a failed PWM write, an
unhandled exception, SIGTERM/SIGINT, and normal exit. The BIOS full-speed
settings sit underneath all of this as the last resort if this service never
runs at all.

Config: /etc/gpu-fan-control.conf. Nothing here needs a code change to tune.
"""

import configparser
import glob
import math
import logging
import os
import signal
import sys
import time

CONFIG_PATH = os.environ.get("GPU_FAN_CONTROL_CONF", "/etc/gpu-fan-control.conf")
FULL_SPEED = 255


class Fatal(Exception):
    """Anything that means we can no longer control the fans safely."""


def find_hwmon(name):
    for path in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
        try:
            with open(os.path.join(path, "name")) as fh:
                if fh.read().strip() == name:
                    return path
        except OSError:
            continue
    return None


def find_amdgpu_hwmon(pci_id):
    pattern = "/sys/bus/pci/devices/%s/hwmon/hwmon*" % pci_id
    matches = sorted(glob.glob(pattern))
    if not matches:
        return None
    return matches[0]


def read_int(path):
    with open(path) as fh:
        return int(fh.read().strip())


class Channel:
    """One blower: its controller channel, its card, and its own control state."""

    def __init__(self, cfg, section, defaults):
        self.name = section
        self.channel = cfg.getint(section, "channel")
        self.pci_id = cfg.get(section, "pci_id")
        self.label = cfg.get(section, "label", fallback=self.pci_id)

        def val(key, getter, fallback=None):
            if cfg.has_option(section, key):
                return getter(section, key)
            return defaults[key] if fallback is None else fallback

        self.curve = parse_curve(val("curve", cfg.get))
        self.junction_offset = val("junction_offset", cfg.getfloat)
        self.target = val("target", cfg.getfloat)
        self.kd = val("kd", cfg.getfloat)
        self.ki = val("ki", cfg.getfloat)
        self.integral_limit = val("integral_limit", cfg.getfloat)
        self.slew_up = val("slew_up", cfg.getfloat)
        self.slew_down = val("slew_down", cfg.getfloat)
        self.dead_band = val("dead_band", cfg.getfloat)
        self.derivative_samples = val("derivative_samples", cfg.getint)
        self.smoothing_tau = val("smoothing_tau", cfg.getfloat)
        self.pwm_min = val("pwm_min", cfg.getint)
        self.pwm_max = val("pwm_max", cfg.getint)

        self.smooth = None         # low-pass filtered control temperature
        self.history = []          # (monotonic seconds, smoothed temperature)
        self.integral = 0.0
        self.pwm = float(FULL_SPEED)   # start full speed, ease down from there
        self.hwmon = None

    def resolve(self):
        self.hwmon = find_amdgpu_hwmon(self.pci_id)
        if not self.hwmon:
            raise Fatal("no amdgpu hwmon for %s" % self.pci_id)

    def read_temps(self):
        if not self.hwmon:
            self.resolve()
        edge = read_int(os.path.join(self.hwmon, "temp1_input")) / 1000.0
        junction = read_int(os.path.join(self.hwmon, "temp2_input")) / 1000.0
        return edge, junction

    def slope(self):
        """Least-squares degrees per second over the retained samples."""
        pts = self.history[-self.derivative_samples:]
        if len(pts) < 3:
            return 0.0
        n = len(pts)
        mean_t = sum(p[0] for p in pts) / n
        mean_y = sum(p[1] for p in pts) / n
        num = sum((p[0] - mean_t) * (p[1] - mean_y) for p in pts)
        den = sum((p[0] - mean_t) ** 2 for p in pts)
        if den == 0:
            return 0.0
        return num / den

    def step(self, now, dt):
        edge, junction = self.read_temps()
        raw = max(edge, junction - self.junction_offset)

        if self.smooth is None:
            self.smooth = raw
        else:
            alpha = 1.0 - math.exp(-dt / self.smoothing_tau) if self.smoothing_tau > 0 else 1.0
            self.smooth += alpha * (raw - self.smooth)
        control = self.smooth

        self.history.append((now, control))
        if len(self.history) > self.derivative_samples * 3:
            del self.history[:-self.derivative_samples * 3]

        rate = self.slope()

        if self.ki:
            self.integral += (control - self.target) * dt
            cap = self.integral_limit / self.ki if self.ki else 0.0
            self.integral = max(-cap, min(cap, self.integral))
        else:
            self.integral = 0.0

        want = curve_value(self.curve, control) + self.kd * rate + self.ki * self.integral
        want = max(self.pwm_min, min(self.pwm_max, want))

        delta = want - self.pwm
        if abs(delta) < self.dead_band:
            want = self.pwm
        else:
            limit = (self.slew_up if delta > 0 else self.slew_down) * dt
            if abs(delta) > limit:
                want = self.pwm + limit * (1 if delta > 0 else -1)

        self.pwm = max(self.pwm_min, min(self.pwm_max, want))
        return {
            "edge": edge, "junction": junction, "raw": raw, "control": control,
            "rate": rate, "pwm": int(round(self.pwm)),
        }


def parse_curve(text):
    """'30:60, 45:95, 60:170, 72:255' -> sorted [(temp, pwm), ...]"""
    points = []
    for item in text.replace("\n", ",").split(","):
        item = item.strip()
        if not item:
            continue
        temp, pwm = item.split(":")
        points.append((float(temp), float(pwm)))
    if len(points) < 2:
        raise Fatal("curve needs at least two points, got %r" % text)
    return sorted(points)


def curve_value(points, temp):
    """Piecewise-linear interpolation, flat outside the ends."""
    if temp <= points[0][0]:
        return points[0][1]
    if temp >= points[-1][0]:
        return points[-1][1]
    for (t0, p0), (t1, p1) in zip(points, points[1:]):
        if t0 <= temp <= t1:
            if t1 == t0:
                return p1
            return p0 + (p1 - p0) * (temp - t0) / (t1 - t0)
    return points[-1][1]


class Controller:
    def __init__(self, cfg):
        self.interval = cfg.getfloat("loop", "interval", fallback=1.0)
        self.log_every = cfg.getfloat("loop", "log_every", fallback=30.0)
        self.write_retries = cfg.getint("loop", "write_retries", fallback=3)
        defaults = {
            "curve": cfg.get("defaults", "curve"),
            "junction_offset": cfg.getfloat("defaults", "junction_offset"),
            "target": cfg.getfloat("defaults", "target"),
            "kd": cfg.getfloat("defaults", "kd"),
            "ki": cfg.getfloat("defaults", "ki"),
            "integral_limit": cfg.getfloat("defaults", "integral_limit"),
            "slew_up": cfg.getfloat("defaults", "slew_up"),
            "slew_down": cfg.getfloat("defaults", "slew_down"),
            "dead_band": cfg.getfloat("defaults", "dead_band"),
            "derivative_samples": cfg.getint("defaults", "derivative_samples"),
            "smoothing_tau": cfg.getfloat("defaults", "smoothing_tau"),
            "pwm_min": cfg.getint("defaults", "pwm_min"),
            "pwm_max": cfg.getint("defaults", "pwm_max"),
        }
        self.channels = [
            Channel(cfg, s, defaults)
            for s in cfg.sections() if s.startswith("channel:")
        ]
        if not self.channels:
            raise Fatal("no [channel:*] sections in %s" % CONFIG_PATH)
        self.hwmon = None

    def resolve(self):
        self.hwmon = find_hwmon("arctic_fan")
        if not self.hwmon:
            raise Fatal("arctic_fan hwmon not present (driver loaded?)")
        for ch in self.channels:
            ch.resolve()

    def write_pwm(self, channel, value):
        path = os.path.join(self.hwmon, "pwm%d" % channel)
        last = None
        for _ in range(self.write_retries):
            try:
                with open(path, "w") as fh:
                    fh.write(str(int(value)))
                return
            except OSError as exc:
                last = exc
                time.sleep(0.05)
        raise Fatal("cannot write %s: %s" % (path, last))

    def full_speed(self):
        """Best effort, never raises. Called from the exit paths."""
        hwmon = self.hwmon or find_hwmon("arctic_fan")
        if not hwmon:
            logging.error("FAIL-SAFE: arctic_fan hwmon gone, cannot set full speed")
            return
        for ch in self.channels:
            for _ in range(5):
                try:
                    with open(os.path.join(hwmon, "pwm%d" % ch.channel), "w") as fh:
                        fh.write(str(FULL_SPEED))
                    break
                except OSError:
                    time.sleep(0.1)
        logging.warning("FAIL-SAFE: all channels set to full speed")

    def run(self):
        self.resolve()
        for ch in self.channels:
            self.write_pwm(ch.channel, FULL_SPEED)
        logging.info("started, interval %.2fs, channels: %s", self.interval,
                     ", ".join("%s ch%d %s" % (c.name, c.channel, c.label)
                               for c in self.channels))
        last = time.monotonic()
        last_log = 0.0
        while True:
            now = time.monotonic()
            dt = min(max(now - last, 1e-3), self.interval * 5)
            last = now
            report = []
            for ch in self.channels:
                state = ch.step(now, dt)
                self.write_pwm(ch.channel, state["pwm"])
                report.append("%s edge=%.0f junc=%.0f raw=%.1f ctl=%.1f d=%+.2fC/s pwm=%d"
                              % (ch.label, state["edge"], state["junction"],
                                 state["raw"], state["control"], state["rate"],
                                 state["pwm"]))
            if now - last_log >= self.log_every:
                logging.info("; ".join(report))
                last_log = now
            time.sleep(max(0.0, self.interval - (time.monotonic() - now)))


def main():
    logging.basicConfig(stream=sys.stdout, level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    cfg = configparser.ConfigParser()
    if not cfg.read(CONFIG_PATH):
        logging.error("cannot read %s", CONFIG_PATH)
        # No config means no control. Leave the fans safe and stop.
        hwmon = find_hwmon("arctic_fan")
        if hwmon:
            for i in range(1, 11):
                try:
                    with open(os.path.join(hwmon, "pwm%d" % i), "w") as fh:
                        fh.write(str(FULL_SPEED))
                except OSError:
                    pass
        return 1
    controller = Controller(cfg)

    def on_signal(signum, _frame):
        logging.warning("signal %d, going to full speed and exiting", signum)
        controller.full_speed()
        sys.exit(0)

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    try:
        controller.run()
    except Exception as exc:                      # noqa: BLE001 - fail safe on anything
        logging.error("FAIL-SAFE trigger: %s", exc)
        controller.full_speed()
        return 1
    finally:
        controller.full_speed()
    return 0


if __name__ == "__main__":
    sys.exit(main())
