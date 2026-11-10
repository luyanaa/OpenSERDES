"""Sample-clock capability gate (item 2 of the PPA audit)."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
GEN = ROOT / "scripts" / "gen_ip.py"


def _spec(tech: str, rate: float, oversample: int) -> dict:
    return {
        "name": "sample_clock_gate",
        "target": "rx",
        "link": {"payload_width": 8, "words": 4, "line_rate_mbps": rate,
                 "coding": "raw", "bit_order": "lsb_first"},
        "tx": {"architecture": "parallel_mux"},
        "rx": {"architecture": "fixed_oversampling", "oversample": oversample,
               "align": "external", "clocking": "independent", "ppm": 0,
               "sync_len": 8, "allow_os2": True},
        "tech": {"tx": tech, "rx": tech},
        "interface": {"preset": "minimal", "data": "valid_ready",
                      "control": "native", "cdc": "none",
                      "enable_mode": "static"},
        "constraints": {"uncertainty_setup_ns": 0.1, "uncertainty_hold_ns": 0.05},
    }


class TestSampleClockGate(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdirs: list[tempfile.TemporaryDirectory] = []

    def tearDown(self) -> None:
        for t in self._tmpdirs:
            t.cleanup()

    def _run(self, spec: dict, *extra: str) -> tuple[int, str, Path | None]:
        tmp = tempfile.TemporaryDirectory()
        self._tmpdirs.append(tmp)
        d = Path(tmp.name)
        spec_path = d / "s.yaml"
        spec_path.write_text(yaml.safe_dump(spec))
        out = d / "out"
        cmd = [sys.executable, str(GEN), "--spec", str(spec_path),
               "--out", str(out), "--force", *extra]
        p = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=120)
        return p.returncode, p.stdout + p.stderr, (out if out.exists() else None)

    def test_gate_rejects_impossible_rx_clock(self) -> None:
        # sky130: 50 Mb/s x OS=8 = 400 MHz > 200 MHz limit.
        code, text, _ = self._run(_spec("sky130", 50, 8))
        self.assertEqual(code, 2)
        self.assertIn("max_sample_clock_mhz", text)
        self.assertIn("400", text)

    def test_relax_limits_warns_instead_of_failing(self) -> None:
        code, text, out = self._run(_spec("sky130", 50, 8), "--relax-limits")
        self.assertEqual(code, 0, text)
        self.assertIsNotNone(out)
        m = json.loads((out / "manifest.json").read_text())
        self.assertTrue(any("relaxed" in w for w in m["warnings"]))

    def test_default_spec_is_within_the_gate(self) -> None:
        # icsprout55 H7CL: 100 Mb/s x OS=4 = 400 MHz == the 400 MHz limit.
        code, text, out = self._run(_spec("icsprout55", 100, 4))
        self.assertEqual(code, 0, text)
        m = json.loads((out / "manifest.json").read_text())
        self.assertEqual(m["clocking"]["sample_clock_mhz"], 400.0)
        self.assertEqual(m["clocking"]["max_sample_clock_mhz"], 400)

    def test_limit_is_recorded_in_manifest(self) -> None:
        code, text, out = self._run(_spec("sky130", 25, 4), "--relax-limits")
        self.assertEqual(code, 0, text)
        m = json.loads((out / "manifest.json").read_text())
        self.assertEqual(m["clocking"]["max_sample_clock_mhz"], 200)
        self.assertEqual(m["clocking"]["sample_clock_mhz"], 100.0)

    def test_all_tech_profiles_declare_a_limit(self) -> None:
        for p in sorted((ROOT / "tech").glob("*/tech.yaml")):
            d = yaml.safe_load(p.read_text())
            self.assertIn("max_sample_clock_mhz", d.get("limits", {}), p.name)


if __name__ == "__main__":
    unittest.main()
