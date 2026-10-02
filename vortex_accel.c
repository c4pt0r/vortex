/* Optional C kernels for vortex.py, loaded through ctypes.
 *
 * Every function mirrors a Python method in vortex.py operation for operation,
 * so results are bit-identical to the pure-Python path: same evaluation order,
 * Python's floor-mod and max() semantics, and no FMA contraction (build with
 * -ffp-contract=off). Change both together; tests compare them exactly.
 */
#include <math.h>
#include <stdlib.h>

#define TAU 6.283185307179586

enum { OCEAN, MONSOON, CYCLONE, JUPITER, SMOKE, RIVER, SHEAR, TURBULENCE,
       TAICHI, SOLUTION, WHIRLPOOL, NEBULA, MATRIX };

static long floor_mod(long a, long b) {
    long r = a % b;
    return r < 0 ? r + b : r;
}

static double py_fmod(double a, double b) {
    double r = fmod(a, b);
    if (r != 0. && ((b < 0) != (r < 0))) r += b;
    return r;
}

/* Field.advance: limited MacCormack transport. */
void vx_advance(int w, int h, int smoke, double dt, const double *old,
                const double *vx, const double *vy, const double *moisture,
                double *forward, double *lows, double *highs, double *result) {
    long top = h - 1, last = w - 1;
    int i = 0;
    for (int row = 0; row < h; row++) {
        for (int col = 0; col < w; col++, i++) {
            double x = col - vx[i] * dt, y = row - vy[i] * dt;
            double fix = floor(x), fiy = floor(y);
            long ix = (long)fix, iy = (long)fiy;
            double fx = x - fix, fy = y - fiy;
            long a, b;
            if (smoke) {
                a = (iy < 0 ? 0 : iy > top ? top : iy) * w;
                b = (iy + 1 < 0 ? 0 : iy + 1 > top ? top : iy + 1) * w;
            } else {
                a = floor_mod(iy, h);
                b = (a < top ? a + 1 : 0) * w;
                a *= w;
            }
            long left = floor_mod(ix, w), right = left < last ? left + 1 : 0;
            double q0 = old[a + left], q1 = old[a + right], q2 = old[b + left], q3 = old[b + right];
            double gx = 1 - fx;
            forward[i] = (q0 * gx + q1 * fx) * (1 - fy) + (q2 * gx + q3 * fx) * fy;
            double lo = q0 <= q1 ? q0 : q1, hi = q0 <= q1 ? q1 : q0;
            double lo2 = q2 <= q3 ? q2 : q3, hi2 = q2 <= q3 ? q3 : q2;
            lows[i] = lo <= lo2 ? lo : lo2;
            highs[i] = hi >= hi2 ? hi : hi2;
        }
    }
    i = 0;
    for (int row = 0; row < h; row++) {
        double feed = 0.;
        if (smoke) {
            double v = ((row + .5) / h - .87) / .13;
            feed = v > 0. ? v : 0.;
        }
        for (int col = 0; col < w; col++, i++) {
            double x = col + vx[i] * dt, y = row + vy[i] * dt;
            double fix = floor(x), fiy = floor(y);
            long ix = (long)fix, iy = (long)fiy;
            double fx = x - fix, fy = y - fiy;
            long a, b;
            if (smoke) {
                a = (iy < 0 ? 0 : iy > top ? top : iy) * w;
                b = (iy + 1 < 0 ? 0 : iy + 1 > top ? top : iy + 1) * w;
            } else {
                a = floor_mod(iy, h);
                b = (a < top ? a + 1 : 0) * w;
                a *= w;
            }
            long left = floor_mod(ix, w), right = left < last ? left + 1 : 0;
            double gx = 1 - fx;
            double reverse = (forward[a + left] * gx + forward[a + right] * fx) * (1 - fy)
                           + (forward[b + left] * gx + forward[b + right] * fx) * fy;
            double advected = forward[i] + .5 * (old[i] - reverse);
            if (advected < lows[i]) advected = lows[i];
            if (advected > highs[i]) advected = highs[i];
            double renewed = advected + dt * .018 * (moisture[i] - advected);
            if (smoke) renewed += dt * feed * 1.5 * (moisture[i] - renewed);
            result[i] = renewed < 0. ? 0. : renewed > 1. ? 1. : renewed;
        }
    }
}

/* Field.stable_tones: rank-match density to the fixed reference tones. */
static const double *sort_keys;

static int by_density(const void *pa, const void *pb) {
    int a = *(const int *)pa, b = *(const int *)pb;
    double x = sort_keys[a], y = sort_keys[b];
    return x < y ? -1 : x > y ? 1 : a - b;
}

void vx_stable_tones(int n, const double *density, const double *reference,
                     const double *prefix, int *order, double *tones) {
    for (int i = 0; i < n; i++) order[i] = i;
    sort_keys = density;
    qsort(order, n, sizeof *order, by_density);
    int start = 0;
    while (start < n) {
        int end = start + 1;
        while (end < n && density[order[end]] == density[order[start]]) end++;
        double tone = end - start > 1 ? (prefix[end] - prefix[start]) / (end - start)
                                      : reference[start];
        for (int rank = start; rank < end; rank++) tones[order[rank]] = tone;
        start = end;
    }
}

/* Field.update_wind. centers holds (cx, cy, radius**2, direction, stretch,
 * sheltered) per moved center; jupiter holds the unmoved main storm (cx, cy, radius). */
void vx_update_wind(int mode, int n, const double *coords, double t, double world_width,
                    int count, int seed, int height, double aspect,
                    int ncenters, const double *centers, const double *jupiter,
                    double *out_vx, double *out_vy) {
    double season = cos(t * TAU / 80);
    for (int i = 0; i < n; i++) {
        double x = coords[2 * i], y = coords[2 * i + 1];
        double ux, uy, spin_scale = 0., inward = 0.;
        switch (mode) {
        case OCEAN: {
            double jet = y - .50 - .12 * sin(x * 4 - t * .025);
            ux = .018 + .080 * exp(-pow(jet / .11, 2));
            uy = .018 * cos(x * 4 - t * .025) * exp(-pow(jet / .2, 2));
            spin_scale = .45;
            break;
        }
        case MONSOON:
            ux = season * (.072 + .020 * sin(y * 8));
            uy = season * .025 + .014 * sin(x * 6 - t * .06);
            spin_scale = .22;
            break;
        case JUPITER: {
            double cx = jupiter[0], cy = jupiter[1], radius = jupiter[2];
            double dx = (x - cx) / 1.9, dy = y - cy;
            double skirt = exp(-(dx * dx + dy * dy) / (radius * radius) * .55);
            double bend = dy * .7 * skirt;
            double slope = -dy * .7 * skirt * 1.1 * dx / (1.9 * radius * radius);
            ux = .050 * sin((y + bend) * 24 + .35 * sin(x * 3 - t * .02));
            uy = -slope * ux + .007 * sin(x * 7 + y * 5 + t * .03);
            spin_scale = .70;
            break;
        }
        case SMOKE: {
            double axis = world_width * .5 + .06 * sin(y * 7 - t * .13);
            double plume = exp(-pow((x - axis) / (.08 + (1 - y) * .22), 2));
            ux = .022 * sin(y * 11 - t * .21) - (x - axis) * .035 * plume;
            uy = -.018 - .080 * plume;
            spin_scale = .36;
            break;
        }
        case RIVER: {
            double phase = x / world_width * 7;
            double channel = .5 + .12 * sin(phase);
            double jet = exp(-pow((y - channel) / .22, 2));
            ux = .022 + .11 * jet;
            uy = .12 * 7 / world_width * cos(phase) * ux;
            double dx = x - world_width * .30, dy = y - channel;
            double obstacle = exp(-pow(dx / .10, 2) - pow(dy / .09, 2));
            ux *= 1 - .85 * obstacle;
            uy += dy * 1.8 * obstacle;
            spin_scale = .58;
            break;
        }
        case SHEAR: {
            double mid = .5 + .035 * sin(x * 4 - t * .09);
            ux = .063 * tanh((y - mid) / .075);
            uy = .012 * sin(x / world_width * TAU * count - t * .12);
            spin_scale = .88;
            break;
        }
        case TURBULENCE: {
            static const double waves[3][3] = {{1., .055, .11}, {2.1, .028, -.17}, {4.3, .014, .23}};
            ux = 0.;
            uy = 0.;
            for (int k = 0; k < 3; k++) {
                double scale = waves[k][0], amplitude = waves[k][1], rate = waves[k][2];
                double kx = TAU * scale * (1 + count * .16) / world_width;
                double ky = TAU * scale;
                double a = x * kx + t * rate + seed, b = y * ky - t * rate * .73;
                double norm = ky > kx ? ky : kx;
                ux += amplitude * ky / norm * sin(a) * cos(b);
                uy -= amplitude * kx / norm * cos(a) * sin(b);
            }
            break;
        }
        case SOLUTION:
            ux = .025 * sin(y * TAU + t * .06);
            uy = .030 * sin(x * 4 - t * .04);
            spin_scale = .28;
            break;
        case WHIRLPOOL:
            ux = 0.;
            uy = 0.;
            spin_scale = 1.3;
            inward = .055;
            break;
        case NEBULA:
            ux = .008 + .018 * sin(y * 8 + t * .035);
            uy = .014 * sin(x * 5 - t * .028);
            spin_scale = .18;
            break;
        case TAICHI:
        case MATRIX:
            ux = 0.;
            uy = mode == TAICHI ? 0. : .2;
            break;
        default: /* cyclone */
            ux = .016 + .008 * sin(y * 9 + t * .045);
            uy = .005 * sin(x * 8 - t * .035);
            spin_scale = .90;
            inward = .014;
        }
        for (int c = 0; c < ncenters; c++) {
            const double *s = centers + 6 * c;
            double cx = s[0], cy = s[1], radius2 = s[2], direction = s[3], stretch = s[4];
            double dx = (x - cx) / stretch, dy = y - cy;
            double r2 = (dx * dx + dy * dy) / radius2;
            double influence = exp(-r2 * .85);
            if (s[5] != 0.) {
                double shelter = 1 - exp(-r2 * r2 * .8);
                ux *= shelter;
                uy *= shelter;
            }
            double spin = direction * spin_scale * influence;
            ux -= (dy * spin + dx * inward * influence) * stretch;
            uy += dx * spin - dy * inward * influence;
        }
        out_vx[i] = ux * height / aspect;
        out_vy[i] = uy * height;
    }
}

/* Field.smooth_noise and Field.fbm. */
static double smooth_noise(const double *lattice, double x, double y) {
    double fix = floor(x), fiy = floor(y);
    long ix = (long)fix, iy = (long)fiy;
    double fx = x - fix, fy = y - fiy;
    fx = fx * fx * (3 - 2 * fx);
    fy = fy * fy * (3 - 2 * fy);
    double a = lattice[floor_mod(iy, 64) * 64 + floor_mod(ix, 64)];
    double b = lattice[floor_mod(iy, 64) * 64 + floor_mod(ix + 1, 64)];
    double c = lattice[floor_mod(iy + 1, 64) * 64 + floor_mod(ix, 64)];
    double d = lattice[floor_mod(iy + 1, 64) * 64 + floor_mod(ix + 1, 64)];
    return (a + (b - a) * fx) * (1 - fy) + (c + (d - c) * fx) * fy;
}

static double fbm(const double *lattice, double x, double y) {
    return smooth_noise(lattice, x, y) * .56
         + smooth_noise(lattice, x * 2.03 + 7, y * 2.03 + 3) * .28
         + smooth_noise(lattice, x * 4.11 + 13, y * 4.11 + 9) * .16;
}

/* Field.pattern_density for taichi. libm's hypot can differ from Python's
 * correctly rounded math.hypot by one ulp; vortex.load_accel allows that here. */
void vx_taichi(int n, const double *coords, const double *lattice, double t,
               double world_width, double radius, double *out) {
    double angle = t * .065, c = cos(angle), sn = sin(angle);
    for (int i = 0; i < n; i++) {
        double x = coords[2 * i], y = coords[2 * i + 1];
        double dx = (x - world_width * .5) / radius, dy = (y - .5) / radius;
        double wx = smooth_noise(lattice, x * 3.2 + t * .018 + 11, y * 3.2 + 17) - .5;
        double wy = smooth_noise(lattice, x * 3.2 + 31, y * 3.2 - t * .014 + 7) - .5;
        double u = dx * c + dy * sn + wx * .18;
        double v = -dx * sn + dy * c + wy * .18;
        double r = hypot(u, v);
        double split = .5 + .5 * tanh(u * 7);
        double upper = .5 + .5 * tanh((.5 - hypot(u, v + .5)) * 9);
        double lower = .5 + .5 * tanh((.5 - hypot(u, v - .5)) * 9);
        split = split * (1 - lower);
        split = split + upper * (1 - split);
        double fog = fbm(lattice, x * 4 + wx * 2 - t * .025, y * 4 + wy * 2 + t * .012);
        double wisps = fbm(lattice, u * 3 + wy + t * .012 + 23, v * 3 + wx + 9);
        double envelope = .5 + .5 * tanh((1 - r + wx * .12) * 6);
        double background = .12 + .40 * fog;
        double eye_upper = .5 + .5 * tanh((.13 - hypot(u, v + .5)) * 12);
        double eye_lower = .5 + .5 * tanh((.13 - hypot(u, v - .5)) * 12);
        split = split * (1 - eye_upper);
        split = split + eye_lower * (1 - split);
        double body = .37 + .49 * split + .08 * (wisps - .5);
        double value = background * (1 - envelope) + body * envelope;
        out[i] = value < 0. ? 0. : value > 1. ? 1. : value;
    }
}

/* vortex.noise */
static double hash_noise(double x, double y) {
    double n = sin(x * 127.1 + y * 311.7) * 43758.5453;
    return n - floor(n);
}

/* Field.pattern_density for matrix. */
void vx_matrix(int w, int h, int count, int seed, double t, double *out) {
    for (int col = 0; col < w; col++) {
        double n = hash_noise(col + 31, seed + 19);
        double period = h * (1.5 + n);
        double head = py_fmod(t * h * (.15 + .28 * n) + hash_noise(col + 91, seed + 7) * period, period);
        double tail = h * (.12 + count * .025 + n * .13);
        double scale = tail * .42 > .5 ? tail * .42 : .5;
        for (int row = 0; row < h; row++) {
            double distance = head - row;
            out[row * w + col] = 0 <= distance && distance < tail ? exp(-distance / scale) : 0.;
        }
    }
}

/* The per-cell glyph and shade selection in Field.render. */
void vx_cells(int n, int matrix, int levels, double drift, const double *tones,
              const int *digits, char *chars, unsigned char *shades) {
    int top = levels - 1;
    for (int i = 0; i < n; i++) {
        double density = tones[i], brightness;
        if (matrix) {
            brightness = density;
        } else {
            brightness = (density - .36) * 2.3;
            brightness = brightness < 0. ? 0. : brightness > 1. ? 1. : brightness;
        }
        if (brightness < .065) {
            chars[i] = brightness < .025 ? ' ' : '.';
            shades[i] = 0;
        } else {
            int shade = (int)(pow(brightness, .85) * levels);
            chars[i] = (char)('0' + (digits[i] + (long)(density * 17 + drift)) % 10);
            shades[i] = (unsigned char)(shade < top ? shade : top);
        }
    }
}

/* Field.color_keys, using palette_band from vortex.py. */
void vx_color_keys(int w, int h, double aspect, double t, int levels, int bands,
                   const double *tones, const unsigned char *shades, unsigned char *keys) {
    for (int i = 0; i < w * h; i++) {
        if (!shades[i]) {
            keys[i] = 0;
            continue;
        }
        double x = (i % w + .5) * aspect / h, y = (i / w + .5) / h;
        double phase = tones[i] * 1.15 + .13 * sin(x * 3.4 + y * 2.1 - t * .08)
                     + .10 * sin(y * 4.8 - x * 1.7 + t * .05) + t * .025;
        long band = (long)floor(py_fmod(phase, 1) * 48);
        if (band > 47) band = 47;
        keys[i] = (unsigned char)(levels + band * bands / 48 * levels + shades[i]);
    }
}
