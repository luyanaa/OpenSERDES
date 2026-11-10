"""Half-rate TX rate mode (item 7 of the PPA audit)."""
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


def _spec(target: str = "tx", enable: str = "static") -> dict:
    return {
        "name": "hr", "target": target,
        "link": {"payload_width": 8, "words": 4, "line_rate_mbps": 50,
                 "coding": "raw", "bit_order": "lsb_first"},
        "tx": {"architecture": "word_streaming", "rate_mode": "half"},
        "rx": {"architecture": "fixed_oversampling", "oversample": 4,
               "align": "external", "clocking": "independent", "ppm": 0,
               "sync_len": 8},
        "tech": {"tx": "sky130", "rx": "sky130"},
        "interface": {"preset": "minimal", "data": "valid_ready",
                      "control": "native", "cdc": "none",
                      "enable_mode": enable},
        "constraints": {"uncertainty_setup_ns": 0.1, "uncertainty_hold_ns": 0.05},
    }


class TestRateMode(unittest.TestCase):
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
            return r, None, None
        m = json.loads((out / "manifest.json").read_text())
        sdc = (out / "constraints" / "pre_pnr.sdc").read_text()
        return r, m, sdc

    def test_half_rate_tx_package(self) -> None:
        r, m, sdc = self._run(_spec())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(m["design"], "serdes_tx_half_rate_digital")
        self.assertEqual(m["tx_rate_mode"], "half")
        self.assertEqual(m["clocking"]["ports"], ["clk_word", "clk_bit"])
        self.assertIn("create_generated_clock -name clk_word", sdc)
        self.assertIn("divide_by 2", sdc)

    def test_half_rate_rejected_for_link(self) -> None:
        r, _, _ = self._run(_spec(target="link"))
        self.assertEqual(r.returncode, 2)
        self.assertIn("only available for target: tx", r.stdout + r.stderr)

    def test_half_rate_rejected_for_rx(self) -> None:
        r, _, _ = self._run(_spec(target="rx"))
        self.assertEqual(r.returncode, 2)

    def test_half_rate_rejects_dynamic_enable(self) -> None:
        r, _, _ = self._run(_spec(enable="dynamic"))
        self.assertEqual(r.returncode, 2)
        self.assertIn("enable_mode", r.stdout + r.stderr)

    def test_invalid_rate_mode_rejected(self) -> None:
        spec = _spec()
        spec["tx"]["rate_mode"] = "quarter"
        r, _, _ = self._run(spec)
        self.assertEqual(r.returncode, 2)
        self.assertIn("rate_mode", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
