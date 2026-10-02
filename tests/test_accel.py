import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import vortex


class AccelTests(unittest.TestCase):
    def test_c_kernels_match_python(self):
        lib = vortex.load_accel()
        if not lib:
            self.skipTest("no C compiler; vortex runs in pure Python")
        for mode in vortex.MODES:
            with self.subTest(mode=mode):
                fast = vortex.Field(48, 20, 3, 1, .5, mode, accel=lib)
                slow = vortex.Field(48, 20, 3, 1, .5, mode, accel=None)
                self.assertEqual((fast.vx, fast.vy), (slow.vx, slow.vy))
                for step in range(1, 9):
                    t, max_step = step * .29, (.055 if step % 2 else .3)
                    frames = fast.frame(t, 18, max_step), slow.frame(t, 18, max_step)
                    self.assertEqual(frames[0], frames[1])
                    self.assertEqual(fast.color_keys(t, frames[0][1], 18, 12),
                                     slow.color_keys(t, frames[1][1], 18, 12))
                # taichi may differ by an ulp: libm hypot vs Python's correctly rounded one.
                tolerance = 1e-12 if mode == "taichi" else 0.
                self.assertTrue(all(abs(a - b) <= tolerance for a, b in zip(fast.density, slow.density)))

    def test_pure_python_override(self):
        code = "import vortex; print(vortex.load_accel() is None)"
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True,
                                env={**os.environ, "VORTEX_PURE_PYTHON": "1"})
        self.assertEqual(result.stdout.strip(), "True")


if __name__ == "__main__":
    unittest.main()
