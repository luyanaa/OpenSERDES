"""sky130 LUT extraction smoke tests (generic demo; ngspice required)."""

from __future__ import annotations

import json
import math
import os
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

    def test_pvt_dry_run(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(ROOT / "verification" / "analog" / "pvt_runner.py"),
             "--dry-run", "--corners", "tt", "ss", "--voltages", "1.8",
             "--temperatures", "25"],
            cwd=ROOT, text=True, capture_output=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("2 points", proc.stdout)

    @unittest.skipUnless(os.environ.get("RUN_SKY130_PVT"),
                         "set RUN_SKY130_PVT=1 for a real PVT point (slow)")
    def test_real_pvt_point(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "pvt.json"
            proc = subprocess.run(
                [sys.executable, str(ROOT / "verification" / "analog" / "pvt_runner.py"),
                 "--corners", "tt", "--voltages", "1.8", "--temperatures", "25",
                 "--out", str(out), "--workdir", str(Path(tmp) / "work")],
                cwd=ROOT, text=True, capture_output=True, timeout=180,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            data = json.loads(out.read_text())
            self.assertEqual(len(data["rows"]), 1)
            self.assertGreater(data["rows"][0]["tx_rise_ns"], 0.0)

    @unittest.skipUnless(os.environ.get("RUN_SKY130_LUT"),
                         "set RUN_SKY130_LUT=1 to run the real sky130 sweep")
    def test_real_sky130_lut(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "sky130.json"
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), "--tx-loads-ff", "50",
                 "--rx-slews-ps", "20", "--out", str(out),
                 "--workdir", str(Path(tmp) / "work")],
                cwd=ROOT, text=True, capture_output=True, timeout=300,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            data = load_lut(out)
            self.assertEqual(data["pdk"], "sky130")
            self.assertTrue(data["model_kind"].startswith("sky130-schematic"))
            self.assertGreater(data["tx"]["rows"][0]["rise_ns"], 0.0)

    def test_tech_configs_are_listed(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--list-tech"],
            cwd=ROOT, text=True, capture_output=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for name in ("sky130", "gf180mcu", "ihp_sg13g2", "generic_demo"):
            self.assertIn(name, proc.stdout)

    @unittest.skipUnless(
        shutil.which("magic") and list(Path.home().glob(
            ".ciel/ciel/sky130/versions/*/skywater-pdk/libraries/sky130_fd_pr/latest")),
        "sky130 PM3 tree or magic not available",
    )
    def test_sky130_tech_check_resolves_magic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "check.json"
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), "--tech", "sky130",
                 "--check-tools", "--out", str(out)],
                cwd=ROOT, text=True, capture_output=True, timeout=120,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            data = json.loads(out.read_text())
            self.assertEqual(data["tech"], "sky130")
            self.assertTrue(data["pex"]["magic"]["available"])
            self.assertEqual(data["magic_tech"], "sky130A")
            self.assertTrue(Path(data["pex"]["magic"]["rcfile"]).is_file())

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
