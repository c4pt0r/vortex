#!/usr/bin/env python3
"""ZERO / VORTEX — thirteen full-terminal ASCII flow studies, using only Python's stdlib.

Run: python3 vortex.py [--mode smoke|river|shear|turbulence|ocean|monsoon|cyclone|jupiter] [--no-hud]
Options: --fps 30 --mono --aspect 0.5 (character width / height) --color psychedelic
Keys: Y taichi; L solution; W whirlpool; N nebula; X matrix.
      O ocean; M monsoon; C cyclone; J Jupiter;
      S smoke; V river; K shear; T turbulence; Tab next.
      1-9 vortex count; R rearrange; Space motion / still; +/- speed;
      P color on / off; [ ] palette; I status line; H help; Q quit.
macOS and Linux: no packages required. Fills the current terminal window.
"""

import argparse
import math
import os
import signal
import sys
import time


def bounded_float(low, high):
    def parse(value):
        number = float(value)
        if not math.isfinite(number) or not low <= number <= high:
            raise argparse.ArgumentTypeError(f"must be between {low} and {high}")
        return number
    return parse


def noise(x, y):
    n = math.sin(x * 127.1 + y * 311.7) * 43758.5453
    return n - math.floor(n)


MODES = ("ocean", "monsoon", "cyclone", "jupiter", "smoke", "river", "shear", "turbulence", "taichi", "solution", "whirlpool", "nebula", "matrix")
MODE_KEYS = {ord("o"): "ocean", ord("m"): "monsoon",
             ord("c"): "cyclone", ord("j"): "jupiter",
             ord("s"): "smoke", ord("v"): "river",
             ord("k"): "shear", ord("t"): "turbulence",
             ord("y"): "taichi", ord("l"): "solution", ord("w"): "whirlpool",
             ord("n"): "nebula", ord("x"): "matrix"}

# Same ramps as web/palette.js. Color affects rendering only, never the flow.
COLOR_BANDS = 48
PALETTES = {
    "psychedelic": ((255, 58, 147), (170, 70, 255), (55, 105, 255), (40, 235, 204), (207, 247, 51), (255, 169, 35)),
    "acid": ((223, 255, 32), (111, 243, 43), (255, 65, 180), (172, 48, 255), (255, 192, 31)),
    "sunset": ((255, 205, 89), (255, 120, 51), (248, 57, 109), (182, 69, 208), (98, 80, 221)),
    "aurora": ((72, 239, 143), (48, 221, 218), (79, 135, 246), (163, 101, 245), (104, 237, 209)),
    "deepsea": ((47, 87, 194), (50, 140, 233), (52, 207, 221), (153, 234, 234), (95, 140, 223)),
    "phosphor": ((67, 203, 77), (113, 238, 93), (196, 255, 136), (105, 224, 145)),
}
PALETTE_NAMES = tuple(PALETTES)


def palette_rgb(band, shade, name="psychedelic"):
    stops = PALETTES.get(name, PALETTES["psychedelic"])
    phase = band % COLOR_BANDS / COLOR_BANDS * len(stops)
    index = math.floor(phase)
    a, b, mix = stops[index], stops[(index + 1) % len(stops)], phase - index
    light = .09 + .91 * (max(0, min(17, shade)) / 17) ** .85
    return tuple(math.floor((x + (y - x) * mix) * light + .5) for x, y in zip(a, b))


def palette_band(x, y, density, t):
    # Broad contour-following color ribbons, with slow liquid-light drift.
    phase = (density * 1.15 + .13 * math.sin(x * 3.4 + y * 2.1 - t * .08)
             + .10 * math.sin(y * 4.8 - x * 1.7 + t * .05) + t * .025)
    return min(COLOR_BANDS - 1, math.floor(phase % 1 * COLOR_BANDS))


def xterm_rgb(index):
    if index < 232:
        steps = (0, 95, 135, 175, 215, 255)
        index -= 16
        return steps[index // 36], steps[index // 6 % 6], steps[index % 6]
    gray = 8 + (index - 232) * 10
    return gray, gray, gray


XTERM_COLORS = [(i, xterm_rgb(i)) for i in range(16, 256)]


def nearest_xterm(rgb):
    # Skip the 16 theme-dependent colors; 16-255 are fixed in every 256-color terminal.
    return min(XTERM_COLORS, key=lambda c: sum((a - b) ** 2 for a, b in zip(c[1], rgb)))[0]


class Field:
    """Artistic cloud-density advection, not a meteorological forecast model.

    A continuous wind field transports grayscale cloud density. Differential
    rotation stretches clouds into filaments; slow condensation replenishes
    detail lost to interpolation. Coordinates account for terminal cell shape.
    """

    def __init__(self, width, height, count=3, seed=0, aspect=.5, mode="cyclone"):
        import random
        if mode not in MODES:
            raise ValueError("unknown flow mode")
        self.mode = mode
        self.width, self.height = width, height
        self.count, self.seed, self.aspect = count, seed, aspect
        self.world_width = width * aspect / height
        self.last_t = None
        self.wind_t = -999.0
        self.vx, self.vy = [], []
        rng = random.Random(seed + 173)
        self.lattice = [rng.random() for _ in range(64 * 64)]
        cols = min(range(1, count + 1), key=lambda c:
                   abs(math.log(max(.001, self.world_width * math.ceil(count / c) / c)))
                   + .15 * (c * math.ceil(count / c) - count))
        rows = math.ceil(count / cols)
        self.centers = []
        for i in range(count):
            row, col = divmod(i, cols)
            row_count = min(cols, count - row * cols)
            cx = (col + .5 + (cols - row_count) * .5) * self.world_width / cols
            cy = (row + .5) / rows
            cx += (rng.random() - .5) * self.world_width / cols * .23
            cy += (rng.random() - .5) / rows * .23
            radius = max(.045, min(self.world_width / cols, 1 / rows) * .58)
            self.centers.append((cx, cy, radius, rng.random() * math.tau,
                                 1 if i % 3 != 2 else -1))
        if mode == "jupiter":
            # One dominant elliptical storm, with optional small flank eddies.
            self.centers = [(self.world_width * .58, .55,
                             min(.22, self.world_width / 3.6), 0., -1)] + [
                (self.world_width * (.12 + .76 * (i / max(1, count - 2))),
                 .21 if i % 2 == 0 else .83, .055, i * 1.7, 1 if i % 2 == 0 else -1)
                for i in range(count - 1)]
        elif mode == "shear":
            self.centers = [((i + .5) * self.world_width / count,
                             .5 + .035 * math.sin(i * 2 + seed),
                             min(.24, self.world_width / count * .48), i * 1.7, -1)
                            for i in range(count)]
        elif mode == "river":
            self.centers = [(self.world_width * (.42 + .5 * i / max(1, count - 1)),
                             .5 + (.14 if i % 2 == 0 else -.14),
                             min(.12, self.world_width * .09), i * 1.8,
                             1 if i % 2 == 0 else -1) for i in range(count)]
        elif mode == "smoke":
            self.centers = [(self.world_width * .5 + (.09 if i % 2 == 0 else -.09),
                             .22 + .55 * i / max(1, count - 1), .14, i * 2.,
                             1 if i % 2 == 0 else -1) for i in range(count)]
        elif mode == "whirlpool":
            self.centers = [(self.world_width * .5, .5, min(.48, self.world_width * .43), 0., 1)]
        self.coordinates = [((x + .5) * aspect / height, (y + .5) / height)
                            for y in range(height) for x in range(width)]
        self.density = []
        self.moisture = []
        self.digits = []
        for i, (x, y) in enumerate(self.coordinates):
            # Start with irregular cloud masses already wound by differential
            # rotation; never generate equally spaced, repeating spiral arms.
            u, v = x, y
            for center_index, (cx, cy, radius, phase, direction) in enumerate(self.centers):
                stretch = 1.9 if mode == "jupiter" and center_index == 0 else 1.
                dx, dy = (x - cx) / stretch, y - cy
                r2 = (dx * dx + dy * dy) / (radius * radius)
                influence = math.exp(-r2 * 1.3)
                angle = direction * 4.3 * influence
                c, s = math.cos(angle), math.sin(angle)
                u += (dx * c - dy * s - dx) * influence * stretch
                v += (dx * s + dy * c - dy) * influence
            warp = self.fbm(u * 3 + 11, v * 3 + 17) - .5
            mass = self.fbm(u * 7 + warp * 2, v * 7 - warp * 1.5)
            front = .5 + .5 * math.sin(y * 10 + x * 2.2 + warp * 4)
            density = max(.0, min(1., (mass - .33) * 2.5 + front * .1))
            if mode == "ocean":
                ribbon = (.5 + .5 * math.sin(v * 26 + warp * 5 + u * 1.7)) ** 2
                density = min(1., max(0., .7 * ribbon + .52 * mass - .13))
            elif mode == "monsoon":
                front = .5 + .5 * math.sin((v - u * .38) * 19 + warp * 5)
                density = min(1., max(0., front * .58 + mass * .7 - .20))
            elif mode == "jupiter":
                cx, cy, radius, _, _ = self.centers[0]
                dx, dy = (x - cx) / 1.9, y - cy
                r = math.hypot(dx, dy) / radius
                mask = math.exp(-r ** 4 * .65)
                # Wind noise into an oval storm; no radial sine / target rings.
                turn = -5.5 * math.exp(-r * r * .55)
                su = (dx * math.cos(turn) - dy * math.sin(turn)) / radius
                sv = (dx * math.sin(turn) + dy * math.cos(turn)) / radius
                cloud = self.fbm(su * 1.7 + 21, sv * 1.7 + 9)
                filaments = self.fbm(su * 6 + cloud, sv * 3 + 31)
                bend = dy * .7 * math.exp(-r * r * .55)
                belt = .5 + .5 * math.sin((y + bend) * 32 + warp * 4 + .6 * math.sin(x * 4))
                background = .13 + .29 * belt + .30 * mass
                storm = .23 + .65 * cloud + .22 * filaments
                density = min(1., max(0., (1 - mask) * background + mask * storm))
            elif mode == "smoke":
                axis = self.world_width * .5 + .055 * math.sin(y * 9 + seed)
                spread = .045 + (1 - y) * .20
                plume = math.exp(-((x - axis) / spread) ** 2)
                density = min(1., plume * (.28 + .85 * mass))
            elif mode == "river":
                channel = .5 + .12 * math.sin(x / self.world_width * 7)
                ribbon = .5 + .5 * math.sin((y - channel) * 70 + warp * 5)
                density = min(1., math.exp(-((y - channel) / .27) ** 4)
                              * (.12 + .55 * ribbon + .32 * mass))
            elif mode == "shear":
                boundary = .5 + .09 * math.sin(x / self.world_width * math.tau * count)
                cloud = math.exp(-((v - boundary) / .22) ** 2)
                density = min(1., cloud * (.22 + .92 * mass))
            elif mode == "turbulence":
                density = min(1., max(0., (self.fbm(x * 9 + warp * 4, y * 9 - warp * 3) - .35) * 3))
            elif mode == "solution":
                interface = .5 + .13 * math.sin(u * 5 + warp * 4)
                droplets = .5 + .5 * math.sin(v * 17 - u * 7 + warp * 6)
                density = min(1., max(0., .28 + .28 * math.tanh((v - interface) * 8) + .40 * droplets))
            elif mode == "whirlpool":
                dx, dy = x - self.world_width * .5, y - .5
                radius = min(.48, self.world_width * .43)
                r = math.hypot(dx, dy) / radius
                angle = math.atan2(dy, dx) + 3.5 * math.log(max(.04, r))
                arms = (.5 + .5 * math.sin(angle * (2 + count // 3) + warp * 2)) ** 2
                density = min(1., math.exp(-r * r * .8) * (.18 + arms * .80 + mass * .18))
            elif mode == "nebula":
                cloud = math.exp(-((y - .5 - .17 * math.sin(x * 3 + warp * 4)) / .30) ** 2)
                density = min(1., max(0., (mass - .34) * 3.6) * cloud)
            self.density.append(density)
            self.moisture.append(density)
            self.digits.append(int(noise(i % width + 9, i // width + 13) * 10))
        if mode in ("taichi", "matrix"):
            self.density = self.pattern_density(0.)
            self.moisture = self.density[:]
        # Fixed reference tones keep cloud cover and dark gaps from drifting
        # as repeated resampling compresses the simulation's density range.
        self.reference_tones = sorted(self.density)
        self.tone_prefix = [0.0]
        for value in self.reference_tones:
            self.tone_prefix.append(self.tone_prefix[-1] + value)
        self.update_wind(0.0)

    def pattern_density(self, t):
        """Mist-veiled yin-yang flow and discrete falling-code trails."""
        result = []
        radius = max(.001, min(.43, self.world_width * .43))
        angle = t * .065
        c, sn = math.cos(angle), math.sin(angle)
        for i, (x, y) in enumerate(self.coordinates):
            if self.mode == "matrix":
                col, row = i % self.width, i // self.width
                n = noise(col + 31, self.seed + 19)
                period = self.height * (1.5 + n)
                head = (t * self.height * (.15 + .28 * n) + noise(col + 91, self.seed + 7) * period) % period
                tail = self.height * (.12 + self.count * .025 + n * .13)
                distance = head - row
                value = math.exp(-distance / max(.5, tail * .42)) if 0 <= distance < tail else 0.
                result.append(value)
                continue
            dx, dy = (x - self.world_width * .5) / radius, (y - .5) / radius
            # Domain warping dissolves the geometric outline into drifting mist.
            wx = self.smooth_noise(x * 3.2 + t * .018 + 11, y * 3.2 + 17) - .5
            wy = self.smooth_noise(x * 3.2 + 31, y * 3.2 - t * .014 + 7) - .5
            u = dx * c + dy * sn + wx * .18
            v = -dx * sn + dy * c + wy * .18
            r = math.hypot(u, v)
            # Opposing rounded lobes preserve the yin-yang S through the haze.
            split = .5 + .5 * math.tanh(u * 7)
            upper = .5 + .5 * math.tanh((.5 - math.hypot(u, v + .5)) * 9)
            lower = .5 + .5 * math.tanh((.5 - math.hypot(u, v - .5)) * 9)
            split = split * (1 - lower)
            split = split + upper * (1 - split)
            fog = self.fbm(x * 4 + wx * 2 - t * .025, y * 4 + wy * 2 + t * .012)
            wisps = self.fbm(u * 3 + wy + t * .012 + 23, v * 3 + wx + 9)
            envelope = .5 + .5 * math.tanh((1 - r + wx * .12) * 6)
            background = .12 + .40 * fog
            eye_upper = .5 + .5 * math.tanh((.13 - math.hypot(u, v + .5)) * 12)
            eye_lower = .5 + .5 * math.tanh((.13 - math.hypot(u, v - .5)) * 12)
            split = split * (1 - eye_upper)
            split = split + eye_lower * (1 - split)
            body = .37 + .49 * split + .08 * (wisps - .5)
            value = background * (1 - envelope) + body * envelope
            result.append(max(0., min(1., value)))
        return result

    def smooth_noise(self, x, y):
        ix, iy = math.floor(x), math.floor(y)
        fx, fy = x - ix, y - iy
        fx, fy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)
        a = self.lattice[(iy % 64) * 64 + ix % 64]
        b = self.lattice[(iy % 64) * 64 + (ix + 1) % 64]
        c = self.lattice[((iy + 1) % 64) * 64 + ix % 64]
        d = self.lattice[((iy + 1) % 64) * 64 + (ix + 1) % 64]
        return (a + (b - a) * fx) * (1 - fy) + (c + (d - c) * fx) * fy

    def fbm(self, x, y):
        return (self.smooth_noise(x, y) * .56
                + self.smooth_noise(x * 2.03 + 7, y * 2.03 + 3) * .28
                + self.smooth_noise(x * 4.11 + 13, y * 4.11 + 9) * .16)

    def update_wind(self, t):
        self.wind_t = t
        centers = [(cx + radius * .14 * math.sin(t * .07 + phase),
                    cy + radius * .12 * math.sin(t * .05 + phase),
                    radius, direction)
                   for cx, cy, radius, phase, direction in self.centers]
        if self.mode == "jupiter":
            cx, cy, radius, _, direction = self.centers[0]
            centers[0] = (cx + radius * .04 * math.sin(t * .035), cy, radius, direction)
        vx, vy = [], []
        # An artistic seasonal cycle, deliberately compressed to 80 sim seconds.
        season = math.cos(t * math.tau / 80)
        for x, y in self.coordinates:
            if self.mode == "ocean":
                jet = y - .50 - .12 * math.sin(x * 4 - t * .025)
                ux = .018 + .080 * math.exp(-(jet / .11) ** 2)
                uy = .018 * math.cos(x * 4 - t * .025) * math.exp(-(jet / .2) ** 2)
                spin_scale, inward = .45, 0.
            elif self.mode == "monsoon":
                ux = season * (.072 + .020 * math.sin(y * 8))
                uy = season * .025 + .014 * math.sin(x * 6 - t * .06)
                spin_scale, inward = .22, 0.
            elif self.mode == "jupiter":
                cx, cy, radius, _, _ = self.centers[0]
                dx, dy = (x - cx) / 1.9, y - cy
                skirt = math.exp(-(dx * dx + dy * dy) / (radius * radius) * .55)
                bend = dy * .7 * skirt
                slope = -dy * .7 * skirt * 1.1 * dx / (1.9 * radius * radius)
                ux = .050 * math.sin((y + bend) * 24 + .35 * math.sin(x * 3 - t * .02))
                uy = -slope * ux + .007 * math.sin(x * 7 + y * 5 + t * .03)
                spin_scale, inward = .70, 0.
            elif self.mode == "smoke":
                axis = self.world_width * .5 + .06 * math.sin(y * 7 - t * .13)
                plume = math.exp(-((x - axis) / (.08 + (1 - y) * .22)) ** 2)
                ux = .022 * math.sin(y * 11 - t * .21) - (x - axis) * .035 * plume
                uy = -.018 - .080 * plume
                spin_scale, inward = .36, 0.
            elif self.mode == "river":
                phase = x / self.world_width * 7
                channel = .5 + .12 * math.sin(phase)
                jet = math.exp(-((y - channel) / .22) ** 2)
                ux = .022 + .11 * jet
                uy = .12 * 7 / self.world_width * math.cos(phase) * ux
                # A smooth deflection splits the current, feeding a rippling wake.
                dx, dy = x - self.world_width * .30, y - channel
                obstacle = math.exp(-(dx / .10) ** 2 - (dy / .09) ** 2)
                ux *= 1 - .85 * obstacle
                uy += dy * 1.8 * obstacle
                spin_scale, inward = .58, 0.
            elif self.mode == "shear":
                mid = .5 + .035 * math.sin(x * 4 - t * .09)
                ux = .063 * math.tanh((y - mid) / .075)
                uy = .012 * math.sin(x / self.world_width * math.tau * self.count - t * .12)
                spin_scale, inward = .88, 0.
            elif self.mode == "turbulence":
                # Curl of time-varying stream functions: interacting eddies at
                # several scales, without a persistent central vortex marker.
                ux, uy = 0., 0.
                for scale, amplitude, rate in ((1., .055, .11), (2.1, .028, -.17), (4.3, .014, .23)):
                    kx = math.tau * scale * (1 + self.count * .16) / self.world_width
                    ky = math.tau * scale
                    a, b = x * kx + t * rate + self.seed, y * ky - t * rate * .73
                    norm = max(kx, ky)
                    ux += amplitude * ky / norm * math.sin(a) * math.cos(b)
                    uy -= amplitude * kx / norm * math.cos(a) * math.sin(b)
                spin_scale, inward = 0., 0.
            elif self.mode == "solution":
                ux = .025 * math.sin(y * math.tau + t * .06)
                uy = .030 * math.sin(x * 4 - t * .04)
                spin_scale, inward = .28, 0.
            elif self.mode == "whirlpool":
                ux, uy, spin_scale, inward = 0., 0., 1.3, .055
            elif self.mode == "nebula":
                ux = .008 + .018 * math.sin(y * 8 + t * .035)
                uy = .014 * math.sin(x * 5 - t * .028)
                spin_scale, inward = .18, 0.
            elif self.mode in ("taichi", "matrix"):
                ux, uy, spin_scale, inward = 0., (0. if self.mode == "taichi" else .2), 0., 0.
            else:
                ux = .016 + .008 * math.sin(y * 9 + t * .045)
                uy = .005 * math.sin(x * 8 - t * .035)
                spin_scale, inward = .90, .014
            for index, (cx, cy, radius, direction) in enumerate(centers):
                stretch = 1.9 if self.mode == "jupiter" and index == 0 else 1.
                dx, dy = (x - cx) / stretch, y - cy
                r2 = (dx * dx + dy * dy) / (radius * radius)
                influence = math.exp(-r2 * .85)
                if self.mode == "jupiter" and index == 0:
                    # Suppress crossing jets within the dominant closed oval.
                    shelter = 1 - math.exp(-r2 * r2 * .8)
                    ux *= shelter
                    uy *= shelter
                spin = direction * spin_scale * influence
                ux -= (dy * spin + dx * inward * influence) * stretch
                uy += dx * spin - dy * inward * influence
            vx.append(ux * self.height / self.aspect)
            vy.append(uy * self.height)
        self.vx, self.vy = vx, vy

    def advance(self, dt):
        # Limited MacCormack transport: forward then backward tracing estimates
        # interpolation error. Local donor bounds prevent ringing/overshoot.
        w, h = self.width, self.height
        old = self.density
        forward, donors = [], []
        for i in range(len(old)):
            x, y = i % w - self.vx[i] * dt, i // w - self.vy[i] * dt
            ix, iy = math.floor(x), math.floor(y)
            fx, fy = x - ix, y - iy
            if self.mode == "smoke":
                # Open vertical plume: no top-to-bottom recirculation.
                a, b = min(h - 1, max(0, iy)) * w, min(h - 1, max(0, iy + 1)) * w
            else:
                a, b = (iy % h) * w, ((iy + 1) % h) * w
            left, right = ix % w, (ix + 1) % w
            q0, q1, q2, q3 = old[a + left], old[a + right], old[b + left], old[b + right]
            forward.append((q0 * (1 - fx) + q1 * fx) * (1 - fy)
                           + (q2 * (1 - fx) + q3 * fx) * fy)
            donors.append((min(q0, q1, q2, q3), max(q0, q1, q2, q3)))
        result = []
        for i, value in enumerate(old):
            x, y = i % w + self.vx[i] * dt, i // w + self.vy[i] * dt
            ix, iy = math.floor(x), math.floor(y)
            fx, fy = x - ix, y - iy
            if self.mode == "smoke":
                # Open vertical plume: no top-to-bottom recirculation.
                a, b = min(h - 1, max(0, iy)) * w, min(h - 1, max(0, iy + 1)) * w
            else:
                a, b = (iy % h) * w, ((iy + 1) % h) * w
            left, right = ix % w, (ix + 1) % w
            reverse = ((forward[a + left] * (1 - fx) + forward[a + right] * fx) * (1 - fy)
                       + (forward[b + left] * (1 - fx) + forward[b + right] * fx) * fy)
            lo, hi = donors[i]
            advected = min(hi, max(lo, forward[i] + .5 * (value - reverse)))
            # Very slow renewal; no directional sharpening or global whitening.
            renewed = advected + dt * .018 * (self.moisture[i] - advected)
            if self.mode == "smoke":
                y = (i // w + .5) / h
                feed = max(0., (y - .87) / .13)
                renewed += dt * feed * 1.5 * (self.moisture[i] - renewed)
            result.append(max(0., min(1., renewed)))
        self.density = result

    def stable_tones(self):
        """Match a fixed initial tonal distribution; never feed it into the wind.

        Rank mapping preserves cloud shapes and restores clear dark gaps rather
        than boosting the whole image. Equal densities share a tone, avoiding
        scan-line patterns in uniform regions. No per-frame random dithering.
        """
        density = self.density
        order = sorted(range(len(density)), key=density.__getitem__)
        tones = [0.0] * len(order)
        start = 0
        while start < len(order):
            end = start + 1
            while end < len(order) and density[order[end]] == density[order[start]]:
                end += 1
            tone = ((self.tone_prefix[end] - self.tone_prefix[start]) / (end - start)
                    if end - start > 1 else self.reference_tones[start])
            for rank in range(start, end):
                tones[order[rank]] = tone
            start = end
        return tones

    def render(self, t, levels):
        procedural = self.mode in ("taichi", "matrix")
        if procedural:
            self.density = self.pattern_density(t)
        if not procedural and self.last_t is not None:
            elapsed = max(0., min(.3, t - self.last_t))
            if elapsed and t - self.wind_t >= .35:
                self.update_wind(t)
            steps = max(1, math.ceil(elapsed / .055))
            if elapsed:
                for _ in range(steps):
                    self.advance(elapsed / steps)
        self.last_t = t
        tones = self.density if self.mode == "matrix" else self.stable_tones()
        self.tones = tones
        for y in range(self.height):
            row = []
            for x in range(self.width):
                i = y * self.width + x
                density = tones[i]
                # Brightness follows transported cloud density only, never center positions.
                brightness = density if self.mode == "matrix" else min(1., max(0., (density - .36) * 2.3))
                if brightness < .065:
                    row.append((' ' if brightness < .025 else '.', 0))
                else:
                    digit = (self.digits[i] + int(density * 17 + t * (8 if self.mode == "matrix" else .16))) % 10
                    shade = min(levels - 1, int(brightness ** .85 * levels))
                    row.append((str(digit), shade))
            yield row

def palette(curses, mono):
    if not mono and curses.has_colors():
        curses.start_color()
        count = min(18, curses.COLOR_PAIRS - 1)
        if curses.COLORS >= 256 and count >= 2:
            attrs = []
            for i in range(count):
                gray = 234 + round(i * 20 / (count - 1))
                curses.init_pair(i + 1, gray, curses.COLOR_BLACK)
                attrs.append(curses.color_pair(i + 1))
            return attrs
        if curses.COLORS >= 8 and curses.COLOR_PAIRS >= 2:
            curses.init_pair(1, curses.COLOR_WHITE, curses.COLOR_BLACK)
            base = curses.color_pair(1)
            return [base | curses.A_DIM, base, base | curses.A_BOLD]
    return [curses.A_DIM, curses.A_NORMAL, curses.A_BOLD]


class ColorPairs:
    """Curses attributes for the selected palette, banded by hue and shaded by density.

    Only the active palette holds color pairs, so every palette fits in the
    256 pairs most terminals offer; switching palettes redefines them.
    """

    def __init__(self, curses, levels, mono):
        self.curses, self.levels, self.name = curses, levels, None
        self.base = levels + 1
        self.bands = 0
        if not mono and curses.has_colors() and curses.COLORS >= 256:
            bands = min(12, (curses.COLOR_PAIRS - self.base) // levels)
            self.bands = bands if bands >= 3 else 0

    def select(self, name):
        if name == self.name:
            return
        for band in range(self.bands):
            for shade in range(self.levels):
                rgb = palette_rgb((band + .5) * COLOR_BANDS / self.bands,
                                  shade * 17 / max(1, self.levels - 1), name)
                self.curses.init_pair(self.base + band * self.levels + shade,
                                      nearest_xterm(rgb), self.curses.COLOR_BLACK)
        self.name = name

    def attr(self, band, shade):
        return self.curses.color_pair(self.base + band * self.bands // COLOR_BANDS * self.levels + shade)


def help_lines(mode, count, speed, paused, color):
    names = [f"{chr(k).upper()} {MODE_KEYS[k]}" for k in sorted(MODE_KEYS, key=lambda k: MODES.index(MODE_KEYS[k]))]
    lines = ["VORTEX  -  keys", "",
             f"{mode.upper()} / {count} / {speed:.1f}x / {'STILL' if paused else 'MOTION'} / {color.upper()}", "",
             "Patterns"]
    lines += ["  " + "".join(name.ljust(14) for name in names[i:i + 3]).rstrip() for i in range(0, len(names), 3)]
    lines += ["", "Controls",
              "  Tab      next pattern",
              "  1-9      vortex count",
              "  R        remix field",
              "  Space    motion / still",
              "  + / -    speed", "",
              "Color",
              "  P        color on / off",
              "  [ / ]    previous / next palette",
              "  " + " ".join(PALETTE_NAMES), "",
              "Screen",
              "  I        status line",
              "  H / Esc  close help",
              "  Q        quit"]
    return lines


def draw_help(screen, curses, lines, width, height, attr):
    inner = max(len(line) for line in lines) + 2
    box_w, box_h = min(width, inner + 2), min(height, len(lines) + 2)
    left, top = max(0, (width - box_w) // 2), max(0, (height - box_h) // 2)
    rows = ["+" + "-" * (box_w - 2) + "+"]
    rows += ["|" + (" " + line).ljust(box_w - 2)[:box_w - 2] + "|" for line in lines[:box_h - 2]]
    rows.append(rows[0])
    for y, text in enumerate(rows[:box_h]):
        try:
            screen.addstr(top + y, left, text[:max(0, width - left - (top + y == height - 1))], attr)
        except curses.error:
            pass


def run(screen, args, curses):
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    screen.nodelay(True)
    screen.keypad(True)
    if hasattr(curses, "set_escdelay"):
        curses.set_escdelay(25)
    attrs = palette(curses, args.mono)
    if not args.mono and curses.has_colors() and curses.COLOR_PAIRS >= 2:
        screen.bkgd(" ", attrs[0])
    colors = ColorPairs(curses, len(attrs), args.mono)
    palette_name = args.color or PALETTE_NAMES[0]
    colored = bool(args.color) and colors.bands > 0
    field = None
    count, seed, mode = args.count, 0, args.mode
    speed, paused, hud, show_help = args.speed, False, not args.no_hud, False
    t, previous = 0.0, time.monotonic()
    dirty = True
    while True:
        started = time.monotonic()
        dt = min(.1, started - previous)
        previous = started
        key = screen.getch()
        while key != -1:
            if key == 27 and show_help:
                show_help = False
                dirty = True
            elif key in (ord('q'), ord('Q'), 27):
                return
            elif key == ord(' '):
                paused = not paused
                dirty = True
            elif key in (ord('+'), ord('='), curses.KEY_UP):
                speed = min(3.0, round(speed + .1, 1))
                dirty = True
            elif key in (ord('-'), ord('_'), curses.KEY_DOWN):
                speed = max(.1, round(speed - .1, 1))
                dirty = True
            elif key == 9 or (key + 32 if 65 <= key <= 90 else key) in MODE_KEYS:
                mode_key = key + 32 if 65 <= key <= 90 else key
                mode = MODES[(MODES.index(mode) + 1) % len(MODES)] if key == 9 else MODE_KEYS[mode_key]
                field = None
                t = 0.0
                dirty = True
            elif ord('1') <= key <= ord('9'):
                count = key - ord('0')
                field = None
                dirty = True
            elif key in (ord('r'), ord('R')):
                seed += 1
                field = None
                dirty = True
            elif key in (ord('h'), ord('H'), ord('?')):
                show_help = not show_help
                dirty = True
            elif key in (ord('i'), ord('I')):
                hud = not hud
                dirty = True
            elif key in (ord('p'), ord('P')):
                colored = not colored and colors.bands > 0
                dirty = True
            elif key in (ord('['), ord(']')) and colors.bands:
                step = 1 if key == ord(']') else -1
                palette_name = PALETTE_NAMES[(PALETTE_NAMES.index(palette_name) + step) % len(PALETTE_NAMES)]
                colored = True
                dirty = True
            elif key == curses.KEY_RESIZE:
                dirty = True
            key = screen.getch()
        height, width = screen.getmaxyx()
        art_height = max(1, height - (1 if hud and height > 1 else 0))
        if field is None or (field.width, field.height) != (width, art_height):
            field = Field(width, art_height, count, seed, args.aspect, mode)
            screen.erase()
            dirty = True
        if not paused:
            t += dt * speed
            dirty = True
        if dirty:
            if colored:
                colors.select(palette_name)
            for y, row in enumerate(field.render(t, len(attrs))):
                if colored:
                    py, offset = (y + .5) / art_height, y * width
                    cells = [attrs[0] if shade == 0 else colors.attr(
                        palette_band((x + .5) * field.aspect / art_height, py, field.tones[offset + x], t), shade)
                        for x, (_, shade) in enumerate(row)]
                else:
                    cells = [attrs[shade] for _, shade in row]
                # Batch neighboring characters with the same attribute.
                x = 0
                while x < width:
                    attr = cells[x]
                    end = x + 1
                    while end < width and cells[end] == attr:
                        end += 1
                    try:
                        screen.addstr(y, x, ''.join(c for c, _ in row[x:end]), attr)
                    except curses.error:
                        # Writing the bottom-right cell can report ERR after drawing;
                        # a concurrent terminal resize can also invalidate coordinates.
                        pass
                    x = end
            color_label = palette_name if colored else "mono"
            if hud and height > 1:
                status = 'STILL' if paused else 'MOTION'
                season_label = (" / OUTBOUND" if math.cos(t * math.tau / 80) >= 0 else " / RETURN") if mode == "monsoon" else ""
                label = f" {mode.upper()}{season_label} / {count} {speed:.1f}x {status} / {color_label.upper()} | H help  SPACE motion/still  P color  [ ] palette  Q quit"
                try:
                    screen.addstr(height - 1, 0, label[:max(0, width - 1)].ljust(max(0, width - 1)), attrs[-1])
                except curses.error:
                    pass
            if show_help:
                draw_help(screen, curses, help_lines(mode, count, speed, paused, color_label), width, height, attrs[-1])
            screen.refresh()
            dirty = False
        time.sleep(max(0, 1 / args.fps - (time.monotonic() - started)))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--mode', choices=MODES, default='cyclone', help='flow study (default: cyclone)')
    parser.add_argument('--count', type=int, choices=range(1, 10), default=3, help='number of vortices (default: 3)')
    parser.add_argument('--aspect', type=bounded_float(.2, 1), default=.5, help='character width / height, default .5')
    parser.add_argument('--speed', type=bounded_float(.1, 3), default=.7)
    parser.add_argument('--fps', type=bounded_float(1, 60), default=30)
    parser.add_argument('--no-hud', action='store_true', help='start with only the animated field')
    parser.add_argument('--mono', action='store_true', help='use monochrome terminal attributes')
    parser.add_argument('--color', choices=PALETTE_NAMES, help='start in a color palette (needs 256 colors)')
    args = parser.parse_args()
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        parser.exit(2, 'Run this script in an interactive terminal, without piping or redirecting output.\n')
    if os.environ.get('TERM', '') in ('', 'dumb'):
        parser.exit(2, 'This terminal needs a cursor-addressable TERM such as xterm-256color.\n')
    try:
        import curses
        import termios
    except ImportError:
        parser.exit(2, 'Python curses is required (included with standard macOS/Linux Python).\n')

    def terminate(signum, frame):
        raise KeyboardInterrupt

    old_term = signal.signal(signal.SIGTERM, terminate)
    terminal_fd = sys.stdin.fileno()
    terminal_settings = termios.tcgetattr(terminal_fd)
    try:
        curses.wrapper(run, args, curses)
    except KeyboardInterrupt:
        pass
    except curses.error as error:
        parser.exit(1, f'Terminal initialization failed: {error}\n')
    finally:
        signal.signal(signal.SIGTERM, old_term)
        termios.tcsetattr(terminal_fd, termios.TCSADRAIN, terminal_settings)


if __name__ == '__main__':
    main()
