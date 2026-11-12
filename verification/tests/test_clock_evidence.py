"""Sample-clock evidence tiers: timing-closed is not physical signoff."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
GEN = ROOT / "scripts" / "gen_ip.py"
BASE = ROOT / "spec" / "bang_bang_4x.yaml"


def _load_gen():
    spec = importlib.util.spec_from_file_location("gen_ip_mod", GEN)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # dataclasses resolve annotations through sys.modules[cls.__module__]
    sys.modules["gen_ip_mod"] = module
    spec.loader.exec_module(module)
    return module


class TestClockEvidence(unittest.TestCase):
    def _run(self, tech: str, line_rate: float,
             constraints: dict | None = None,
             target: str | None = None) -> tuple[int, str]:
        spec = yaml.safe_load(BASE.read_text())
        spec["name"] = f"clock_evidence_{tech.replace('-', '_')}_{int(line_rate * 100)}"
        spec["link"]["line_rate_mbps"] = line_rate
        spec["tech"] = {"tx": tech, "rx": tech}
        if constraints:
            spec.setdefault("constraints", {}).update(constraints)
        if target is not None:
            spec["target"] = target
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "spec.yaml"
            path.write_text(yaml.safe_dump(spec))
            proc = subprocess.run(
                [sys.executable, str(GEN), "--spec", str(path), "--check"],
                cwd=ROOT, text=True, capture_output=True, timeout=120)
        return proc.returncode, proc.stdout + proc.stderr

    def _check(self, tech: str, line_rate: float,
               constraints: dict | None = None) -> dict:
        code, text = self._run(tech, line_rate, constraints)
        self.assertEqual(code, 0, text)
        return json.loads(text)

    def _clock_warnings(self, payload: dict) -> list[str]:
        return [w for w in payload["warnings"] if "sample clock" in w]

    def test_sky130_closed_point_is_physical_signoff(self) -> None:
        payload = self._check("sky130", 10.0)      # 40 MHz sample
        evidence = payload["clock_evidence"]
        self.assertEqual(evidence["tier"], "physical_signoff")
        self.assertEqual(self._clock_warnings(payload), [])

    def test_sky130_above_closure_is_unverified(self) -> None:
        payload = self._check("sky130", 12.5)      # 50 MHz sample
        self.assertEqual(payload["clock_evidence"]["tier"], "none")
        text = " ".join(self._clock_warnings(payload))
        self.assertIn("exceeds the verified post-route timing closure", text)
        self.assertIn("45.45 MHz", text)
        self.assertIn("CP-implied 53 MHz", text)

    def test_gf180_has_no_closure(self) -> None:
        payload = self._check("gf180mcuD", 7.5)    # 30 MHz sample
        self.assertEqual(payload["clock_evidence"]["tier"], "none")
        text = " ".join(self._clock_warnings(payload))
        self.assertIn("no verified post-route timing closure", text)
        self.assertIn("CP-implied 34 MHz", text)

    def test_ihp_has_no_closure(self) -> None:
        payload = self._check("ihp-sg13g2", 20.0)  # 80 MHz sample
        self.assertEqual(payload["clock_evidence"]["tier"], "none")
        text = " ".join(self._clock_warnings(payload))
        self.assertIn("no verified post-route timing closure", text)
        self.assertIn("CP-implied 91 MHz", text)

    def test_icsprout_has_no_closure(self) -> None:
        payload = self._check("icsprout55", 16.25)  # 65 MHz sample
        self.assertEqual(payload["clock_evidence"]["tier"], "none")
        text = " ".join(self._clock_warnings(payload))
        self.assertIn("no verified post-route timing closure", text)
        self.assertIn("CP-implied 73 MHz", text)

    def test_require_physical_signoff_accepts_sky130_40mhz(self) -> None:
        payload = self._check("sky130", 10.0,
                              {"require_clock_evidence": "physical_signoff"})
        evidence = payload["clock_evidence"]
        self.assertTrue(evidence["satisfied"])
        self.assertEqual(evidence["required"], "physical_signoff")

    def test_require_physical_signoff_rejects_sky130_50mhz(self) -> None:
        code, text = self._run(
            "sky130", 12.5,
            {"require_clock_evidence": "physical_signoff"})
        self.assertEqual(code, 2, text)
        self.assertIn("below constraints.require_clock_evidence='physical_signoff'", text)
        self.assertIn("tier 'none'", text)

    def test_require_timing_closed_rejects_gf180(self) -> None:
        code, text = self._run(
            "gf180mcuD", 7.5,
            {"require_clock_evidence": "timing_closed"})
        self.assertEqual(code, 2, text)
        self.assertIn("below constraints.require_clock_evidence='timing_closed'", text)
        self.assertIn("source=build/pnr/cross_pdk.json", text)

    def test_invalid_clock_evidence_requirement_rejected(self) -> None:
        code, text = self._run(
            "sky130", 10.0,
            {"require_clock_evidence": "silicon"})
        self.assertEqual(code, 2, text)
        self.assertIn("must be none, timing_closed or physical_signoff", text)

    def test_clock_evidence_requirement_on_tx_rejected(self) -> None:
        code, text = self._run(
            "sky130", 10.0,
            {"require_clock_evidence": "physical_signoff"}, target="tx")
        self.assertEqual(code, 2, text)
        self.assertIn("only applies to target: link or rx", text)

    def test_timing_only_tier_is_not_physical(self) -> None:
        gen = _load_gen()
        evidence = {"timing_closed_mhz": 40.0, "physical_signoff_mhz": None}
        self.assertEqual(gen.clock_tier(evidence, 40.0), "timing_closed")
        self.assertEqual(gen.clock_tier(evidence, 41.0), "none")
        self.assertEqual(gen.clock_tier(None, 10.0), "none")


if __name__ == "__main__":
    unittest.main()
