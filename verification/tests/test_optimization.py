"""Optimization weights / budgets / aggregation (item 4 of the PPA audit)."""
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
BASE = yaml.safe_load((ROOT / "spec/power_tradeoff.yaml").read_text())


class TestOptimization(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.d = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, spec: dict):
        p = self.d / "s.yaml"
        p.write_text(yaml.safe_dump(spec))
        out = self.d / ("out_%d" % len(list(self.d.glob("out_*"))))
        r = subprocess.run([sys.executable, str(GEN), "--spec", str(p),
                            "--out", str(out), "--force"], cwd=ROOT,
                           text=True, capture_output=True, timeout=120)
        if r.returncode != 0:
            return r, None
        return r, json.loads((out / "manifest.json").read_text())

    def test_budgets_reject_a_candidate(self) -> None:
        r, m = self._run(BASE)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        sel = m["architecture"]["tx"]["selection"]
        self.assertIn("word_streaming: area exceeds budget", sel["budget_rejections"])
        self.assertEqual(m["architecture"]["tx"]["name"], "word_group_capture")
        self.assertEqual(m["optimization"]["aggregation"], "minmax")

    def test_power_only_weights_change_the_winner(self) -> None:
        spec = json.loads(json.dumps(BASE))
        spec["optimization"]["budgets"] = {}
        spec["optimization"]["weights"] = {"area": 0.0, "power": 1.0, "latency": 0.0}
        spec["optimization"]["aggregation"] = "weighted_sum"
        r, m = self._run(spec)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(m["architecture"]["tx"]["name"], "word_streaming")

    def test_lexicographic_uses_weight_order(self) -> None:
        spec = json.loads(json.dumps(BASE))
        spec["optimization"]["budgets"] = {}
        spec["optimization"]["aggregation"] = "lexicographic"
        r, m = self._run(spec)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(m["architecture"]["tx"]["selection"]["aggregation"],
                         "lexicographic")
        # power has the highest weight (3.0), so the lower-clock-load candidate
        # wins under lexicographic ordering.
        self.assertEqual(m["architecture"]["tx"]["name"], "word_streaming")

    def test_no_budget_gives_a_two_candidate_frontier(self) -> None:
        spec = json.loads(json.dumps(BASE))
        spec["optimization"]["budgets"] = {}
        r, m = self._run(spec)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(sorted(m["architecture"]["tx"]["frontier"]),
                         ["word_group_capture", "word_streaming"])

    def test_unknown_weight_key_is_rejected(self) -> None:
        spec = json.loads(json.dumps(BASE))
        spec["optimization"]["weights"] = {"area": 1.0, "bogus": 2.0}
        r, _ = self._run(spec)
        self.assertEqual(r.returncode, 2)
        self.assertIn("not in objectives", r.stdout + r.stderr)

    def test_negative_weight_is_rejected(self) -> None:
        spec = json.loads(json.dumps(BASE))
        spec["optimization"]["weights"] = {"area": -1.0}
        r, _ = self._run(spec)
        self.assertEqual(r.returncode, 2)

    def test_all_zero_weights_are_rejected(self) -> None:
        spec = json.loads(json.dumps(BASE))
        spec["optimization"]["weights"] = {"area": 0.0, "power": 0.0, "latency": 0.0}
        r, _ = self._run(spec)
        self.assertEqual(r.returncode, 2)

    def test_unknown_aggregation_is_rejected(self) -> None:
        spec = json.loads(json.dumps(BASE))
        spec["optimization"]["aggregation"] = "magic"
        r, _ = self._run(spec)
        self.assertEqual(r.returncode, 2)

    def test_restricted_os2_candidate_is_reachable(self) -> None:
        spec = {
            "name": "os2", "target": "rx",
            "link": {"payload_width": 8, "words": 4, "line_rate_mbps": 25,
                     "coding": "raw", "bit_order": "lsb_first"},
            "tx": {"architecture": "parallel_mux"},
            "rx": {"architecture": "auto", "oversample": 2, "align": "external",
                   "clocking": "independent", "ppm": 0, "allow_os2": True,
                   "sync_len": 8},
            "tech": {"tx": "sky130", "rx": "sky130"},
            "interface": {"preset": "minimal", "data": "valid_ready",
                          "control": "native", "cdc": "none",
                          "enable_mode": "static"},
            "constraints": {"uncertainty_setup_ns": 0.1, "uncertainty_hold_ns": 0.05},
        }
        r, m = self._run(spec)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(m["architecture"]["rx"]["name"], "fixed_oversampling_os2")
        self.assertAlmostEqual(m["architecture"]["rx"]["metrics"]["power"], 0.5, places=2)


if __name__ == "__main__":
    unittest.main()
