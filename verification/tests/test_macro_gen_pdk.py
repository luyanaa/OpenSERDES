"""Cross-PDK analog macro generation: devices, sizing and honest status."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GEN = ROOT / "verification" / "analog" / "macro_gen.py"


class TestMacroGenPdk(unittest.TestCase):
    def _run(self, tech: str, topology: str = "tx_inv_chain"):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "macro"
            proc = subprocess.run(
                [sys.executable, str(GEN), "--tech", tech,
                 "--topology", topology, "--out", str(out)],
                cwd=ROOT, text=True, capture_output=True, timeout=120)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            spice = (out / "circuit.spice").read_text()
            manifest = json.loads((out / "manifest.json").read_text())
            return spice, manifest

    def test_gf180_uses_pdk_devices_and_macro_length(self) -> None:
        spice, manifest = self._run("gf180mcu")
        self.assertIn("nfet_03v3", spice)
        self.assertIn("pfet_03v3", spice)
        self.assertIn("l=0.28u", spice)
        self.assertIn("nf=1", spice)
        self.assertEqual(manifest["status"], "SCHEMATIC_ONLY")
        self.assertFalse(manifest["physical_signoff_eligible"])
        self.assertEqual(manifest["device_models"]["nmos"], "nfet_03v3")

    def test_ihp_uses_ng_finger_parameter(self) -> None:
        spice, manifest = self._run("ihp_sg13g2", "rx_res_fb_inv")
        self.assertIn("sg13_lv_nmos", spice)
        self.assertIn("sg13_lv_pmos", spice)
        self.assertIn("ng=1", spice)
        self.assertIn("l=0.13u", spice)
        self.assertEqual(manifest["status"], "SCHEMATIC_ONLY")

    def test_icsprout_uses_pdk_subckts(self) -> None:
        spice, manifest = self._run("icsprout55")
        self.assertIn("nm1p2_svt_lp", spice)
        self.assertIn("pm1p2_svt_lp", spice)
        self.assertIn("l=0.06u", spice)
        self.assertIn("nf=1", spice)
        self.assertEqual(manifest["device_models"]["pmos"], "pm1p2_svt_lp")

    def test_etri_uses_model_cards_not_subckts(self) -> None:
        spice, manifest = self._run("etri050")
        self.assertIn("nmos4", spice)
        self.assertIn("pmos4", spice)
        self.assertIn("W=5u L=0.6u", spice)
        self.assertNotIn("nf=", spice)
        self.assertEqual(manifest["status"], "SCHEMATIC_ONLY")

    def test_freepdk_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(
                [sys.executable, str(GEN), "--tech", "freepdk45",
                 "--topology", "tx_inv_chain", "--out", str(Path(tmp) / "x")],
                cwd=ROOT, text=True, capture_output=True, timeout=120)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("capabilities.macro_generation=false", proc.stderr)

    def test_list_tech_reports_capability(self) -> None:
        proc = subprocess.run([sys.executable, str(GEN), "--list-tech"],
                              cwd=ROOT, text=True, capture_output=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        for tech in ("sky130", "gf180mcu", "ihp_sg13g2", "icsprout55", "etri050"):
            self.assertIn(f"{tech}: status=", proc.stdout)
        self.assertIn("freepdk45: status=scaffold macro_generation=False", proc.stdout)


if __name__ == "__main__":
    unittest.main()
