"""Tests for the RATIO-aware PPA model (item 1 of the PPA audit)."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import ppa_model

GEN = ROOT / "scripts" / "gen_ip.py"


class TestPpaModel(unittest.TestCase):
    def test_cell_model_reproduces_measured_points(self) -> None:
        # Measured Yosys generic-gate counts at RATIO=32 (8x4) and 256 (32x8).
        measured = {
            "parallel_mux": (109, 582),
            "word_group_capture": (142, 626),
            "word_streaming": (308, 1977),
            "fixed_oversampling": (218, 758),
            "adaptive_oversampling": (847, 1382),
            "source_sync": (120, 656),
            "bang_bang_oversampling": (1411, 1956),
        }
        for arch, (at32, at256) in measured.items():
            est32 = ppa_model.estimate(arch, 32, 4, 25.0, "rx")["cells"]
            est256 = ppa_model.estimate(arch, 256, 4, 25.0, "rx")["cells"]
            # The fit is exact at the two measured points; allow 1% slack.
            self.assertAlmostEqual(est32, at32, delta=max(2.0, at32 * 0.01))
            self.assertAlmostEqual(est256, at256, delta=max(4.0, at256 * 0.01))

    def test_bang_bang_costs_more_area_than_fixed(self) -> None:
        # The placeholder (150 cells) claimed the 4x bang-bang loop was the
        # cheapest option.  Measured: 1567 cells at RATIO=32, ~7x the fixed
        # 4x loop, because of the 24/25-bit signed loop filter.  Its only win
        # is clock load (DFF x f_clock).  Guard against reintroducing the
        # claim that 4x sampling is also cheaper in area.
        fixed = ppa_model.relative_metrics("rx", "fixed_oversampling", 32, 4, 25.0)
        bb = ppa_model.relative_metrics("rx", "bang_bang_oversampling", 32, 4, 25.0)
        self.assertGreater(bb["area"], fixed["area"] * 5.0)
        adaptive8 = ppa_model.relative_metrics("rx", "adaptive_oversampling", 32, 8, 25.0)
        self.assertLess(bb["power"], adaptive8["power"])

    def test_word_streaming_is_not_smaller_than_parallel_mux(self) -> None:
        # The old arch_db claimed area 0.80 for word_streaming.  Synthesis says
        # it is ~2.8x the cells; the model must not reintroduce the inversion.
        a = ppa_model.relative_metrics("tx", "parallel_mux", 32, 4, 25.0)
        b = ppa_model.relative_metrics("tx", "word_streaming", 32, 4, 25.0)
        self.assertGreater(b["area"], a["area"] * 2.0)

    def test_adaptive_clock_load_exceeds_fixed(self) -> None:
        fixed = ppa_model.relative_metrics("rx", "fixed_oversampling", 32, 4, 25.0)
        adaptive = ppa_model.relative_metrics("rx", "adaptive_oversampling", 32, 8, 25.0)
        self.assertGreater(adaptive["power"], fixed["power"] * 2.5)
        self.assertGreater(adaptive["area"], fixed["area"] * 2.5)

    def test_source_sync_saves_clock_power(self) -> None:
        fixed = ppa_model.relative_metrics("rx", "fixed_oversampling", 32, 4, 25.0)
        src = ppa_model.relative_metrics("rx", "source_sync", 32, 1, 25.0)
        self.assertLess(src["power"], 0.25)
        self.assertLess(src["area"], fixed["area"])

    def test_reference_is_unity(self) -> None:
        for kind in ("tx", "rx"):
            arch, os = ppa_model.REFERENCE[kind]
            m = ppa_model.relative_metrics(kind, arch, 32, os, 25.0)
            for v in m.values():
                self.assertAlmostEqual(v, 1.0, places=3)

    def test_model_confidence_is_not_measured(self) -> None:
        self.assertEqual(ppa_model.MODEL_SOURCE.startswith("scripts/ppa_model.py"), True)
        with tempfile.TemporaryDirectory() as tmp:
            spec = Path(tmp) / "s.yaml"
            spec.write_text((ROOT / "spec/default.yaml").read_text())
            out = Path(tmp) / "out"
            p = subprocess.run([sys.executable, str(GEN), "--spec", str(spec),
                                "--out", str(out), "--force"], cwd=ROOT,
                               text=True, capture_output=True, timeout=120)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            m = json.loads((out / "manifest.json").read_text())
            for side in ("tx", "rx"):
                self.assertEqual(m["architecture"][side]["metrics_source"], "ppa_model")
                self.assertEqual(m["architecture"][side]["confidence"],
                                 "modeled_yosys_cells")
                self.assertIn("estimate", m["architecture"][side]["ppa_model"])
            self.assertTrue(m["ppa_model"]["not_measured"])
            self.assertNotIn("measured_post_route",
                             str(m["architecture"]["rx"]["confidence"]))


if __name__ == "__main__":
    unittest.main()
