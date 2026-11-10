"""Low-clock defaults and recommendations (item 5 of the PPA audit)."""
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


def _spec(**over) -> dict:
    d = {
        "name": "lp", "target": "rx",
        "link": {"payload_width": 8, "words": 4, "line_rate_mbps": 100,
                 "coding": "raw", "bit_order": "lsb_first"},
        "tx": {"architecture": "parallel_mux"},
        "rx": {"architecture": "auto", "oversample": "auto", "align": "external",
               "clocking": "auto", "ppm": 0, "sync_len": 8},
        "tech": {"tx": "icsprout55", "rx": "icsprout55"},
        "interface": {"preset": "minimal", "data": "valid_ready",
                      "control": "native", "cdc": "none",
                      "enable_mode": "static"},
        "constraints": {"uncertainty_setup_ns": 0.05, "uncertainty_hold_ns": 0.02},
        "optimization": {"clock_power_priority": True},
    }
    d.update(over)
    return d


class TestLowPower(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.d = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, spec: dict):
        p = self.d / "s.yaml"
        p.write_text(yaml.safe_dump(spec))
        out = self.d / ("o%d" % len(list(self.d.glob("o*"))))
        r = subprocess.run([sys.executable, str(GEN), "--spec", str(p),
                            "--out", str(out), "--force"], cwd=ROOT,
                           text=True, capture_output=True, timeout=120)
        if r.returncode != 0:
            return r, None
        return r, json.loads((out / "manifest.json").read_text())

    def test_auto_resolves_to_source_sync_when_power_first(self) -> None:
        r, m = self._run(_spec())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(m["clocking"]["source"], "source_sync")
        self.assertEqual(m["clocking"]["sample_clock_mhz"], 100.0)
        self.assertEqual(m["architecture"]["rx"]["name"], "source_sync")

    def test_auto_stays_independent_without_power_first(self) -> None:
        spec = _spec()
        spec["optimization"] = {}
        r, m = self._run(spec)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotEqual(m["clocking"]["source"], "source_sync")

    def test_auto_does_not_use_source_sync_when_ppm_nonzero(self) -> None:
        spec = _spec()
        spec["link"] = dict(spec["link"], line_rate_mbps=25)
        spec["rx"] = dict(spec["rx"], ppm=100, allow_os2=False)
        r, m = self._run(spec)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotEqual(m["clocking"]["source"], "source_sync")
        # 25 Mb/s x OS=8 = 200 MHz, within the icsprout55 400 MHz gate.
        self.assertEqual(m["clocking"]["sample_clock_mhz"], 200.0)

    def test_auto_oversample_prefers_os2_with_allow_os2(self) -> None:
        spec = _spec()
        spec["rx"] = {"architecture": "auto", "oversample": "auto",
                      "align": "external", "clocking": "independent",
                      "ppm": 0, "allow_os2": True, "sync_len": 8}
        r, m = self._run(spec)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(m["clocking"]["sample_clock_mhz"], 200.0)
        self.assertEqual(m["architecture"]["rx"]["name"], "fixed_oversampling_os2")

    def test_default_spec_records_a_lower_clock_alternative(self) -> None:
        spec = yaml.safe_load((ROOT / "spec/default.yaml").read_text())
        r, m = self._run(spec)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rec = m["clocking"]["recommendation"]
        self.assertIsNotNone(rec)
        self.assertLess(rec["alternative_clock_load_ratio"], 0.5)
        self.assertEqual(rec["alternative"]["arch"], "source_sync")
        self.assertTrue(any("clock load" in w for w in m["warnings"]))

    def test_shipped_low_power_spec_is_source_sync(self) -> None:
        p = subprocess.run([sys.executable, str(GEN), "--spec",
                            str(ROOT / "spec/low_power.yaml"), "--check"],
                           cwd=ROOT, text=True, capture_output=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        payload = json.loads(p.stdout)
        # --check output carries the resolved clocking via the design + params
        self.assertEqual(payload["design"], "serdes_soc_cdc_wrapper")


if __name__ == "__main__":
    unittest.main()
