"""sky130 LUT extraction smoke tests (generic demo; ngspice required)."""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from verification.analog.lut import load_lut, rx_delay_ns, tx_rise_ns, tx_tau_ns

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "verification" / "analog" / "extract_lut.py"


@unittest.skipUnless(shutil.which("ngspice"), "ngspice not installed")
class TestAnalogLut(unittest.TestCase):
    def test_demo_lut_extracts_and_maps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "lut.json"
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), "--demo",
                 "--tx-loads-ff", "10", "50", "100",
                 "--rx-slews-ps", "20", "50", "100",
                 "--out", str(out), "--workdir", str(Path(tmp) / "work")],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            data = load_lut(out)
            self.assertEqual(data["model_kind"], "generic-demo")
            self.assertEqual(len(data["tx"]["rows"]), 3)
            rises = [tx_rise_ns(data, load) for load in (10, 50, 100)]
            self.assertTrue(all(r is not None and r > 0 for r in rises))
            self.assertLess(rises[0], rises[1])
            self.assertLess(rises[1], rises[2])
            tau = tx_tau_ns(data, 50)
            self.assertIsNotNone(tau)
            self.assertAlmostEqual(tau, rises[1] / math.log(4.0), places=12)
            delays = [rx_delay_ns(data, s) for s in (20, 50, 100)]
            self.assertTrue(all(d is not None and d > 0 for d in delays))

    def test_placeholder_models_are_rejected(self) -> None:
        placeholder = Path("/opt/ALIGN-pdk-sky130/SKY130_PDK/models.sp")
        if not placeholder.is_file():
            self.skipTest("placeholder sky130 model file not present")
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), "--model", str(placeholder),
                 "--out", str(Path(tmp) / "lut.json"),
                 "--workdir", str(Path(tmp) / "work")],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("placeholder", proc.stderr)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
