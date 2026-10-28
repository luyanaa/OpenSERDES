"""MC/PEX contract tests (no long PDK sweeps)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MC = ROOT / "verification" / "analog" / "mc_runner.py"
PEX = ROOT / "verification" / "analog" / "pex_characterize.py"


class TestMcPexContracts(unittest.TestCase):
    def test_mc_dry_run_and_pdk_refusal(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(MC), "--dry-run", "--samples", "3"],
            cwd=ROOT, text=True, capture_output=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("NOT PDK statistical MC", proc.stdout)
        pdk = subprocess.run(
            [sys.executable, str(MC), "--mode", "pdk", "--dry-run"],
            cwd=ROOT, text=True, capture_output=True,
        )
        self.assertEqual(pdk.returncode, 2)
        self.assertIn("not available", pdk.stderr)

    def test_pex_dry_run_contract(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(PEX), "--dry-run", "--pex-netlist", "/tmp/x.sp",
             "--top", "tx_driver", "--ports", "in=a,out=z,vdd=v, vss=g"],
            cwd=ROOT, text=True, capture_output=True,
        )
        # The awkward space in the port list should still be parsed; if not,
        # the process must fail cleanly rather than silently guessing.
        self.assertIn(proc.returncode, (0, 2), proc.stdout + proc.stderr)

    def test_pex_vs_schematic_comparison(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            schem = Path(tmp) / "s.json"
            pex = Path(tmp) / "p.json"
            out = Path(tmp) / "cmp.json"
            schem.write_text(json.dumps({
                "tx": {"rows": [{"load_ff": 50, "rise_ns": 0.2, "fall_ns": 0.18}]},
                "rx": {"rows": [{"slew_ps": 20, "tphl_ns": 0.06, "tplh_ns": 0.07}]},
            }))
            pex.write_text(json.dumps({
                "tx": {"rows": [{"load_ff": 50, "rise_ns": 0.22, "fall_ns": 0.20}]},
                "rx": {"rows": [{"slew_ps": 20, "tphl_ns": 0.07, "tplh_ns": 0.08}]},
            }))
            proc = subprocess.run(
                [sys.executable, str(PEX), "--schematic-lut", str(schem),
                 "--pex-lut", str(pex), "--out", str(out)],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            data = json.loads(out.read_text())
            self.assertEqual(len(data["rows"]), 4)
            rise = [r for r in data["rows"] if r["metric"] == "rise_ns"][0]
            self.assertAlmostEqual(rise["delta"], 0.02, places=9)


if __name__ == "__main__":
    unittest.main()
