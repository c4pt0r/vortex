#!/usr/bin/env python3
"""ZERO / VORTEX — thirteen full-terminal ASCII flow studies, using only Python's stdlib.

Run: python3 vortex.py [--mode smoke|river|shear|turbulence|ocean|monsoon|cyclone|jupiter] [--no-hud]
Options: --fps 15 --idle-fps 5 --mono --aspect 0.5 (character width / height) --color psychedelic
Keys: Y taichi; L solution; W whirlpool; N nebula; X matrix.
      O ocean; M monsoon; C cyclone; J Jupiter;
      S smoke; V river; K shear; T turbulence; Tab next.
      1-9 vortex count; R rearrange; Space motion / still; +/- speed;
      P color on / off; [ ] palette; I status line; H help; Q quit.
macOS and Linux: no packages required. Fills the current terminal window.
"""

import argparse
import math
import re
from array import array
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
DIGITS = "0123456789"
SAME_KEY_RUNS = re.compile(rb"(.)\1*", re.S)

# Terminal focus reports (xterm mode 1004), which curses delivers as kxIN / kxOUT.
FOCUS_REPORTING_ON, FOCUS_REPORTING_OFF = "\033[?1004h", "\033[?1004l"
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


ACCEL_KERNELS = {
    "vx_advance": "iiid" + "p" * 8,
    "vx_stable_tones": "i" + "p" * 5,
    "vx_update_wind": "iipddiiidipppp",
    "vx_taichi": "ippddd" + "p",
    "vx_matrix": "iiiidp",
    "vx_cells": "iiidpppp",
    "vx_color_keys": "iiddii" + "ppp",
}
_accel = None


def load_accel():
    """The optional C kernels in vortex_accel.c, or None to stay in pure Python.

    Compiled once with the system C compiler into ~/.cache/vortex, keyed by the
    source hash, and trusted only after matching the Python path exactly.
    VORTEX_PURE_PYTHON=1 disables it.
    """
    global _accel
    if _accel is None:
        _accel = False
        if not os.environ.get("VORTEX_PURE_PYTHON"):
            try:
                lib = _build_accel()
                if lib is not None and _accel_matches_python(lib):
                    _accel = lib
            except Exception:
                pass
    return _accel or None


def _build_accel():
    import ctypes
    import hashlib
    import platform
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path
    source = Path(__file__).resolve().with_name("vortex_accel.c")
    if not source.is_file():
        return None
    digest = hashlib.sha256(source.read_bytes() + platform.machine().encode()).hexdigest()[:16]
    cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "vortex"
    library = cache / f"vortex_accel-{digest}.so"
    if not library.exists():
        compiler = os.environ.get("CC") or shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
        if not compiler:
            return None
        cache.mkdir(parents=True, exist_ok=True)
        fd, partial = tempfile.mkstemp(dir=cache, suffix=".so")
        os.close(fd)
        try:
            # -ffp-contract=off keeps results bit-identical to Python (no fused multiply-add).
            subprocess.run([compiler, "-O2", "-shared", "-fPIC", "-ffp-contract=off",
                            "-o", partial, str(source), "-lm"],
                           check=True, capture_output=True, timeout=60)
            os.replace(partial, library)
        finally:
            if os.path.exists(partial):
                os.unlink(partial)
    lib = ctypes.CDLL(str(library))
    types = {"i": ctypes.c_int, "d": ctypes.c_double, "p": ctypes.c_void_p}
    for name, signature in ACCEL_KERNELS.items():
        kernel = getattr(lib, name)
        kernel.argtypes = [types[code] for code in signature]
        kernel.restype = None
    return lib


def _accel_matches_python(lib):
    # Frames must match exactly. Density must too, except taichi, where libm's
    # hypot may differ from Python's correctly rounded math.hypot by one ulp; that
    # pattern is recomputed every frame, so the difference never accumulates.
    for mode in MODES:
        fields = [Field(13, 7, 3, 2, .5, mode, accel=accel) for accel in (lib, None)]
        frames = [[(f.frame(step * .37, 18), f.color_keys(step * .37, f.frame(step * .37, 18)[1], 18, 12))
                   for step in range(6)] for f in fields]
        tolerance = 1e-12 if mode == "taichi" else 0.
        if frames[0] != frames[1] or any(abs(a - b) > tolerance
                                         for a, b in zip(fields[0].density, fields[1].density)):
            return False
    return True


def _address(buffer):
    return buffer.buffer_info()[0]


class Field:
    """Artistic cloud-density advection, not a meteorological forecast model.

    A continuous wind field transports grayscale cloud density. Differential
    rotation stretches clouds into filaments; slow condensation replenishes
    detail lost to interpolation. Coordinates account for terminal cell shape.
    """

    def __init__(self, width, height, count=3, seed=0, aspect=.5, mode="cyclone", accel=True):
        import random
        if mode not in MODES:
            raise ValueError("unknown flow mode")
        # accel: True loads the C kernels when available, None forces pure Python,
        # or a loaded library (used by the self-check).
        self.accel = load_accel() if accel is True else accel
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
        if self.accel:
            out = array("d", bytes(8 * self.width * self.height))
            if self.mode == "matrix":
                self.accel.vx_matrix(self.width, self.height, self.count, self.seed, t, _address(out))
            else:
                self.accel.vx_taichi(len(out), _address(self._coordinate_array()),
                                     _address(self._cached_array("lattice")), t, self.world_width,
                                     max(.001, min(.43, self.world_width * .43)), _address(out))
            return out.tolist()
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
        if self.accel:
            jupiter = self.mode == "jupiter"
            stencils = array("d", [value for index, (cx, cy, radius, direction) in enumerate(centers)
                                   for value in (cx, cy, radius * radius, direction,
                                                 1.9 if jupiter and index == 0 else 1.,
                                                 1. if jupiter and index == 0 else 0.)])
            main = array("d", self.centers[0][:3] if self.centers else (0., 0., 1.))
            n = self.width * self.height
            vx, vy = array("d", bytes(8 * n)), array("d", bytes(8 * n))
            self.accel.vx_update_wind(MODES.index(self.mode), n, _address(self._coordinate_array()), t,
                                      self.world_width, self.count, self.seed, self.height, self.aspect,
                                      len(centers), _address(stencils), _address(main), _address(vx), _address(vy))
            self.vx, self.vy = vx.tolist(), vy.tolist()
            self._wind_arrays = (self.vx, self.vy, vx, vy)
            return
        vx, vy = [], []
        mode, world_width, count, seed = self.mode, self.world_width, self.count, self.seed
        height, aspect = self.height, self.aspect
        sin, cos, exp, tanh = math.sin, math.cos, math.exp, math.tanh
        jupiter = mode == "jupiter"
        stencils = [(cx, cy, radius * radius, direction, 1.9 if jupiter and index == 0 else 1., jupiter and index == 0)
                    for index, (cx, cy, radius, direction) in enumerate(centers)]
        # An artistic seasonal cycle, deliberately compressed to 80 sim seconds.
        season = cos(t * math.tau / 80)
        for x, y in self.coordinates:
            if mode == "ocean":
                jet = y - .50 - .12 * sin(x * 4 - t * .025)
                ux = .018 + .080 * exp(-(jet / .11) ** 2)
                uy = .018 * cos(x * 4 - t * .025) * exp(-(jet / .2) ** 2)
                spin_scale, inward = .45, 0.
            elif mode == "monsoon":
                ux = season * (.072 + .020 * sin(y * 8))
                uy = season * .025 + .014 * sin(x * 6 - t * .06)
                spin_scale, inward = .22, 0.
            elif mode == "jupiter":
                cx, cy, radius, _, _ = self.centers[0]
                dx, dy = (x - cx) / 1.9, y - cy
                skirt = exp(-(dx * dx + dy * dy) / (radius * radius) * .55)
                bend = dy * .7 * skirt
                slope = -dy * .7 * skirt * 1.1 * dx / (1.9 * radius * radius)
                ux = .050 * sin((y + bend) * 24 + .35 * sin(x * 3 - t * .02))
                uy = -slope * ux + .007 * sin(x * 7 + y * 5 + t * .03)
                spin_scale, inward = .70, 0.
            elif mode == "smoke":
                axis = world_width * .5 + .06 * sin(y * 7 - t * .13)
                plume = exp(-((x - axis) / (.08 + (1 - y) * .22)) ** 2)
                ux = .022 * sin(y * 11 - t * .21) - (x - axis) * .035 * plume
                uy = -.018 - .080 * plume
                spin_scale, inward = .36, 0.
            elif mode == "river":
                phase = x / world_width * 7
                channel = .5 + .12 * sin(phase)
                jet = exp(-((y - channel) / .22) ** 2)
                ux = .022 + .11 * jet
                uy = .12 * 7 / world_width * cos(phase) * ux
                # A smooth deflection splits the current, feeding a rippling wake.
                dx, dy = x - world_width * .30, y - channel
                obstacle = exp(-(dx / .10) ** 2 - (dy / .09) ** 2)
                ux *= 1 - .85 * obstacle
                uy += dy * 1.8 * obstacle
                spin_scale, inward = .58, 0.
            elif mode == "shear":
                mid = .5 + .035 * sin(x * 4 - t * .09)
                ux = .063 * tanh((y - mid) / .075)
                uy = .012 * sin(x / world_width * math.tau * count - t * .12)
                spin_scale, inward = .88, 0.
            elif mode == "turbulence":
                # Curl of time-varying stream functions: interacting eddies at
                # several scales, without a persistent central vortex marker.
                ux, uy = 0., 0.
                for scale, amplitude, rate in ((1., .055, .11), (2.1, .028, -.17), (4.3, .014, .23)):
                    kx = math.tau * scale * (1 + count * .16) / world_width
                    ky = math.tau * scale
                    a, b = x * kx + t * rate + seed, y * ky - t * rate * .73
                    norm = max(kx, ky)
                    ux += amplitude * ky / norm * sin(a) * cos(b)
                    uy -= amplitude * kx / norm * cos(a) * sin(b)
                spin_scale, inward = 0., 0.
            elif mode == "solution":
                ux = .025 * sin(y * math.tau + t * .06)
                uy = .030 * sin(x * 4 - t * .04)
                spin_scale, inward = .28, 0.
            elif mode == "whirlpool":
                ux, uy, spin_scale, inward = 0., 0., 1.3, .055
            elif mode == "nebula":
                ux = .008 + .018 * sin(y * 8 + t * .035)
                uy = .014 * sin(x * 5 - t * .028)
                spin_scale, inward = .18, 0.
            elif mode in ("taichi", "matrix"):
                ux, uy, spin_scale, inward = 0., (0. if mode == "taichi" else .2), 0., 0.
            else:
                ux = .016 + .008 * sin(y * 9 + t * .045)
                uy = .005 * sin(x * 8 - t * .035)
                spin_scale, inward = .90, .014
            for cx, cy, radius2, direction, stretch, sheltered in stencils:
                dx, dy = (x - cx) / stretch, y - cy
                r2 = (dx * dx + dy * dy) / radius2
                influence = exp(-r2 * .85)
                if sheltered:
                    # Suppress crossing jets within the dominant closed oval.
                    shelter = 1 - exp(-r2 * r2 * .8)
                    ux *= shelter
                    uy *= shelter
                spin = direction * spin_scale * influence
                ux -= (dy * spin + dx * inward * influence) * stretch
                uy += dx * spin - dy * inward * influence
            vx.append(ux * height / aspect)
            vy.append(uy * height)
        self.vx, self.vy = vx, vy

    def _coordinate_array(self):
        cached = getattr(self, "_coordinates", None)
        if cached is None or cached[0] is not self.coordinates:
            cached = (self.coordinates, array("d", [v for point in self.coordinates for v in point]))
            self._coordinates = cached
        return cached[1]

    def _cached_array(self, name, typecode="d"):
        # Arrays for inputs that rarely change; rebuilt if the list is replaced.
        values = getattr(self, name)
        cached = getattr(self, "_array_" + name, None)
        if cached is None or cached[0] is not values:
            cached = (values, array(typecode, values))
            setattr(self, "_array_" + name, cached)
        return cached[1]

    def advance(self, dt):
        if self.accel:
            n = len(self.density)
            wind = getattr(self, "_wind_arrays", None)
            if wind and wind[0] is self.vx and wind[1] is self.vy:
                vx, vy = wind[2], wind[3]
            else:
                vx, vy = array("d", self.vx), array("d", self.vy)
            old, scratch = array("d", self.density), array("d", bytes(8 * n * 4))
            base, size = _address(scratch), 8 * n
            self.accel.vx_advance(self.width, self.height, self.mode == "smoke", dt, _address(old),
                                  _address(vx), _address(vy), _address(self._cached_array("moisture")),
                                  base, base + size, base + 2 * size, base + 3 * size)
            self.density = scratch[3 * n:].tolist()
            return
        # Limited MacCormack transport: forward then backward tracing estimates
        # interpolation error. Local donor bounds prevent ringing/overshoot.
        # Hot loop: locals and inline clamps instead of attribute lookups and min/max.
        w, h = self.width, self.height
        old, vx, vy, moisture = self.density, self.vx, self.vy, self.moisture
        smoke = self.mode == "smoke"
        floor, top, last = math.floor, h - 1, w - 1
        n = len(old)
        forward, lows, highs = [0.] * n, [0.] * n, [0.] * n
        i = 0
        for row in range(h):
            for col in range(w):
                x, y = col - vx[i] * dt, row - vy[i] * dt
                ix, iy = floor(x), floor(y)
                fx, fy = x - ix, y - iy
                if smoke:
                    # Open vertical plume: no top-to-bottom recirculation.
                    a = (0 if iy < 0 else top if iy > top else iy) * w
                    b = (0 if iy + 1 < 0 else top if iy + 1 > top else iy + 1) * w
                else:
                    a = iy % h
                    b = (a + 1 if a < top else 0) * w
                    a *= w
                left = ix % w
                right = left + 1 if left < last else 0
                q0, q1, q2, q3 = old[a + left], old[a + right], old[b + left], old[b + right]
                gx = 1 - fx
                forward[i] = (q0 * gx + q1 * fx) * (1 - fy) + (q2 * gx + q3 * fx) * fy
                lo, hi = (q0, q1) if q0 <= q1 else (q1, q0)
                lo2, hi2 = (q2, q3) if q2 <= q3 else (q3, q2)
                lows[i] = lo if lo <= lo2 else lo2
                highs[i] = hi if hi >= hi2 else hi2
                i += 1
        result = [0.] * n
        i = 0
        for row in range(h):
            feed = max(0., ((row + .5) / h - .87) / .13) if smoke else 0.
            for col in range(w):
                x, y = col + vx[i] * dt, row + vy[i] * dt
                ix, iy = floor(x), floor(y)
                fx, fy = x - ix, y - iy
                if smoke:
                    a = (0 if iy < 0 else top if iy > top else iy) * w
                    b = (0 if iy + 1 < 0 else top if iy + 1 > top else iy + 1) * w
                else:
                    a = iy % h
                    b = (a + 1 if a < top else 0) * w
                    a *= w
                left = ix % w
                right = left + 1 if left < last else 0
                gx = 1 - fx
                reverse = ((forward[a + left] * gx + forward[a + right] * fx) * (1 - fy)
                           + (forward[b + left] * gx + forward[b + right] * fx) * fy)
                advected = forward[i] + .5 * (old[i] - reverse)
                lo, hi = lows[i], highs[i]
                if advected < lo:
                    advected = lo
                if advected > hi:
                    advected = hi
                # Very slow renewal; no directional sharpening or global whitening.
                renewed = advected + dt * .018 * (moisture[i] - advected)
                if smoke:
                    renewed += dt * feed * 1.5 * (moisture[i] - renewed)
                result[i] = 0. if renewed < 0. else 1. if renewed > 1. else renewed
                i += 1
        self.density = result

    def stable_tones(self):
        """Match a fixed initial tonal distribution; never feed it into the wind.

        Rank mapping preserves cloud shapes and restores clear dark gaps rather
        than boosting the whole image. Equal densities share a tone, avoiding
        scan-line patterns in uniform regions. No per-frame random dithering.
        """
        if self.accel:
            n = len(self.density)
            density, order, tones = array("d", self.density), array("i", bytes(4 * n)), array("d", bytes(8 * n))
            self.accel.vx_stable_tones(n, _address(density), _address(self._cached_array("reference_tones")),
                                       _address(self._cached_array("tone_prefix")), _address(order), _address(tones))
            return tones.tolist()
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

    def _simulate(self, t, max_step):
        procedural = self.mode in ("taichi", "matrix")
        if procedural:
            self.density = self.pattern_density(t)
        if not procedural and self.last_t is not None:
            elapsed = max(0., min(.3, t - self.last_t))
            if elapsed and t - self.wind_t >= .35:
                self.update_wind(t)
            steps = max(1, math.ceil(elapsed / max_step))
            if elapsed:
                for _ in range(steps):
                    self.advance(elapsed / steps)
        self.last_t = t
        self.tones = self.density if self.mode == "matrix" else self.stable_tones()
        return self.tones

    def frame(self, t, levels, max_step=.055):
        """Advance to time t; return the grid as (text, shades), one glyph and one shade byte per cell."""
        tones = self._simulate(t, max_step)
        matrix, n = self.mode == "matrix", len(tones)
        drift = t * (8 if matrix else .16)
        if self.accel:
            # Keep every buffer referenced for the call; C only sees addresses.
            source, chars, shades = array("d", tones), array("B", bytes(n)), array("B", bytes(n))
            self.accel.vx_cells(n, matrix, levels, drift, _address(source),
                                _address(self._cached_array("digits", "i")), _address(chars), _address(shades))
            return chars.tobytes().decode("ascii"), shades.tobytes()
        digits, top = self.digits, levels - 1
        chars, shades = [], bytearray(n)
        append = chars.append
        for i, density in enumerate(tones):
            # Brightness follows transported cloud density only, never center positions.
            if matrix:
                brightness = density
            else:
                brightness = (density - .36) * 2.3
                brightness = 0. if brightness < 0. else 1. if brightness > 1. else brightness
            if brightness < .065:
                append(' ' if brightness < .025 else '.')
            else:
                shade = int(brightness ** .85 * levels)
                append(DIGITS[(digits[i] + int(density * 17 + drift)) % 10])
                shades[i] = shade if shade < top else top
        return "".join(chars), bytes(shades)

    def render(self, t, levels, max_step=.055):
        """Advance to time t; return rows of (glyph, shade) cells."""
        text, shades = self.frame(t, levels, max_step)
        w = self.width
        return [list(zip(text[i:i + w], shades[i:i + w])) for i in range(0, len(text), w)]

    def color_keys(self, t, shades, levels, bands):
        """Per-cell attribute index for color: 0 for blank cells, otherwise
        levels + hue_band * levels + shade, with hue bands from palette_band."""
        n = len(shades)
        if self.accel:
            tones, cells, keys = array("d", self.tones), array("B", shades), array("B", bytes(n))
            self.accel.vx_color_keys(self.width, self.height, self.aspect, t, levels, bands,
                                     _address(tones), _address(cells), _address(keys))
            return keys.tobytes()
        keys, w, h, aspect = bytearray(n), self.width, self.height, self.aspect
        for i, shade in enumerate(shades):
            if shade:
                band = palette_band((i % w + .5) * aspect / h, (i // w + .5) / h, self.tones[i], t)
                keys[i] = levels + band * bands // COLOR_BANDS * levels + shade
        return bytes(keys)


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

    def table(self, gray):
        """Attributes indexed by Field.color_keys: gray shades, then hue bands x shades."""
        return gray + [self.curses.color_pair(self.base + key) for key in range(self.bands * self.levels)]


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
    count, seed, mode = args.count, 0, args.mode
    speed, paused, hud, show_help = args.speed, False, not args.no_hud, False
    # Ask the terminal to report focus changes so an unfocused window can drop to
    # --idle-fps. Only when terminfo names the reports (kxIN / kxOUT); otherwise
    # the raw sequence would start with Esc and quit.
    focused = True
    if curses.tigetstr("kxIN"):
        sys.stdout.write(FOCUS_REPORTING_ON)
        sys.stdout.flush()
    field = None
    t, previous = 0.0, time.monotonic()
    dirty = True
    while True:
        started = time.monotonic()
        dt = min(.1, started - previous)
        previous = started
        key = screen.getch()
        while key != -1:
            name = curses.keyname(key) if key > 255 else b""
            if name in (b"kxIN", b"kxOUT"):
                focused = name == b"kxIN"
            elif key == 27 and show_help:
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
            # Unfocused windows take one coarse transport step per frame to save CPU.
            text, shades = field.frame(t, len(attrs), .055 if focused else .3)
            if colored:
                keys, table = field.color_keys(t, shades, len(attrs), colors.bands), colors.table(attrs)
            else:
                keys, table = shades, attrs
            for y in range(art_height):
                start = y * width
                row_keys = keys[start:start + width]
                # One addstr per run of neighboring cells with the same attribute.
                for run in SAME_KEY_RUNS.finditer(row_keys):
                    a, b = run.span()
                    try:
                        screen.addstr(y, a, text[start + a:start + b], table[row_keys[a]])
                    except curses.error:
                        # Writing the bottom-right cell can report ERR after drawing;
                        # a concurrent terminal resize can also invalidate coordinates.
                        pass
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
        fps = args.fps if focused else min(args.fps, args.idle_fps)
        time.sleep(max(0, 1 / fps - (time.monotonic() - started)))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--mode', choices=MODES, default='cyclone', help='flow study (default: cyclone)')
    parser.add_argument('--count', type=int, choices=range(1, 10), default=3, help='number of vortices (default: 3)')
    parser.add_argument('--aspect', type=bounded_float(.2, 1), default=.5, help='character width / height, default .5')
    parser.add_argument('--speed', type=bounded_float(.1, 3), default=.7)
    parser.add_argument('--fps', type=bounded_float(1, 60), default=15, help='target frame rate (default: 15)')
    parser.add_argument('--idle-fps', type=bounded_float(1, 60), default=5,
                        help='frame rate while the terminal window is unfocused (default: 5)')
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
        sys.stdout.write(FOCUS_REPORTING_OFF)
        sys.stdout.flush()
        signal.signal(signal.SIGTERM, old_term)
        termios.tcsetattr(terminal_fd, termios.TCSADRAIN, terminal_settings)


if __name__ == '__main__':
    main()
