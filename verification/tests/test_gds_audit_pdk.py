import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from verification.analog.gds_audit import TECH_LAYERS, audit


class TestGdsAuditPdkLayers(unittest.TestCase):
    def make_gds(self, td: Path, layers: list[tuple[int, int]]) -> Path:
        script = td / "make.py"
        gds = td / "layout.gds"
        body = "\n".join(
            f"c.shapes(ly.layer({layer}, {dt})).insert(pya.Box(0, 0, 1000, 1000))"
            for layer, dt in layers
        )
        script.write_text(
            "import pya\n"
            "ly=pya.Layout(); ly.dbu=0.005\n"
            "c=ly.create_cell('audit_cell')\n"
            f"{body}\n"
            f"ly.write('{gds}')\n"
            "print('AUDIT_GDS_OK')\n"
        )
        subprocess.run(["klayout", "-b", "-r", str(script)],
                       check=True, capture_output=True, text=True)
        return gds

    def test_each_pdk_uses_its_own_required_layers(self):
        if shutil.which("klayout") is None:
            self.skipTest("klayout not installed")
        for tech, spec in TECH_LAYERS.items():
            with self.subTest(tech=tech), tempfile.TemporaryDirectory() as td:
                gds = self.make_gds(Path(td), list(spec["required"]))
                result = audit(gds, tech)
                self.assertTrue(result["physical"], result)
                self.assertEqual(result["tech"], tech)
                self.assertEqual(result["missing_physical_layers"], [])

    def test_foreign_pdk_layers_are_not_accepted(self):
        if shutil.which("klayout") is None:
            self.skipTest("klayout not installed")
        gf = TECH_LAYERS["gf180mcu"]["required"]
        with tempfile.TemporaryDirectory() as td:
            gds = self.make_gds(Path(td), list(gf))
            result = audit(gds, "sky130")
            self.assertFalse(result["physical"])
            self.assertEqual(result["tech"], "sky130")
            self.assertTrue(result["missing_physical_layers"])

    def test_unknown_pdk_is_rejected(self):
        with self.assertRaises(ValueError):
            audit(Path("does-not-exist.gds"), "not_a_pdk")


if __name__ == "__main__":
    unittest.main()
