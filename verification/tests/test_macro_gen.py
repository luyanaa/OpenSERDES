import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GEN = ROOT / "verification/analog/macro_gen.py"


class TestMacroGen(unittest.TestCase):
    def run_gen(self, *args):
        return subprocess.run([sys.executable, str(GEN), *args],
                              text=True, capture_output=True, cwd=ROOT)

    def test_sky130_tx_generation_has_contract_and_blackbox(self):
        with tempfile.TemporaryDirectory() as td:
            p = self.run_gen("--tech", "sky130", "--topology", "tx_inv_chain",
                             "--out", td + "/tx")
            self.assertEqual(p.returncode, 0, p.stderr)
            spice = Path(td, "tx/circuit.spice").read_text()
            self.assertIn(".subckt tx_inv_chain", spice)
            self.assertEqual(spice.count("XMN0_"), 1)
            self.assertEqual(spice.count("XMN1_"), 3)
            self.assertEqual(spice.count("XMN2_"), 9)
            self.assertEqual(spice.count("XMP0_"), 2)
            self.assertEqual(spice.count("XMP1_"), 6)
            self.assertEqual(spice.count("XMP2_"), 18)
            manifest = json.loads(Path(td, "tx/manifest.json").read_text())
            self.assertEqual(manifest["views"]["layout"]["status"], "missing")
            self.assertEqual(manifest["views"]["liberty"]["status"], "missing")
            self.assertIn("no fabricated", manifest["note"])
            self.assertIn("(* blackbox *)", Path(td, "tx/macro.v").read_text())

    def test_sky130_rx_generation_preserves_feedback_topology(self):
        with tempfile.TemporaryDirectory() as td:
            p = self.run_gen("--tech", "sky130", "--topology", "rx_res_fb_inv",
                             "--out", td + "/rx")
            self.assertEqual(p.returncode, 0, p.stderr)
            spice = Path(td, "rx/circuit.spice").read_text()
            self.assertIn(".subckt rx_res_fb_inv", spice)
            self.assertEqual(spice.count("XMFB"), 2)
            self.assertIn("fb", spice)
            manifest = json.loads(Path(td, "rx/manifest.json").read_text())
            self.assertEqual(manifest["feedback"]["series_devices"], 2)
            self.assertEqual(manifest["views"]["layout"]["status"], "missing")

    def test_custom_name_is_used_in_spice_and_verilog(self):
        with tempfile.TemporaryDirectory() as td:
            p = self.run_gen("--tech", "sky130", "--topology", "tx_inv_chain",
                             "--name", "custom_tx", "--out", td + "/tx")
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn(".subckt custom_tx VDD VSS IN OUT",
                          Path(td, "tx/circuit.spice").read_text())
            self.assertIn("module custom_tx", Path(td, "tx/macro.v").read_text())

    def test_physical_layout_is_accepted(self):
        if shutil.which("klayout") is None:
            self.skipTest("klayout not installed")
        with tempfile.TemporaryDirectory() as td:
            p = self.run_gen("--tech", "sky130", "--topology", "tx_inv_chain",
                             "--layout-gds", "analog/macros/tx_inv_chain_1_3_9/layout.gds",
                             "--out", td + "/tx")
            self.assertEqual(p.returncode, 0, p.stderr)

    def test_abstract_layout_is_rejected(self):
        if shutil.which("klayout") is None:
            self.skipTest("klayout not installed")
        with tempfile.TemporaryDirectory() as td:
            script = Path(td) / "abstract.py"
            gds = Path(td) / "abstract.gds"
            script.write_text(f"""import pya\nly=pya.Layout(); c=ly.create_cell('abstract')\nc.shapes(ly.layer(68,20)).insert(pya.Box(0,0,1000,1000))\nly.write('{gds}')\n""")
            subprocess.run(["klayout", "-b", "-r", str(script)], check=True,
                           capture_output=True)
            p = self.run_gen("--tech", "sky130", "--topology", "tx_inv_chain",
                             "--layout-gds", str(gds),
                             "--out", td + "/tx")
            self.assertNotEqual(p.returncode, 0)
            self.assertIn("not a physical transistor layout", p.stderr + p.stdout)

    def test_scaffold_pdk_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            p = self.run_gen("--tech", "gf180mcu", "--topology", "tx_inv_chain",
                             "--out", td + "/gf")
            self.assertNotEqual(p.returncode, 0)
            self.assertIn("fail-closed", (p.stderr + p.stdout).lower())
            self.assertFalse(Path(td, "gf/circuit.spice").exists())


if __name__ == "__main__":
    unittest.main()
