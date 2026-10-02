import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import vortex


class PluginTests(unittest.TestCase):
    def test_manifest_matches_vortex(self):
        manifest = json.loads((ROOT / "manifest.json").read_text())
        schema = {f["key"]: f for f in manifest["barWidget"]["schema"]}
        self.assertEqual(schema["mode"]["options"], ["random", *vortex.MODES])
        self.assertEqual(schema["color"]["options"], ["off", *vortex.PALETTE_NAMES])
        widget = (ROOT / manifest["entryPoints"]["barWidget"]).read_text()
        for name in (*vortex.MODES, *vortex.PALETTE_NAMES):
            self.assertIn(f'"{name}"', widget)


if __name__ == "__main__":
    unittest.main()
