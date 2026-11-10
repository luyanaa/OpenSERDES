"""Ping-pong TX bank variant (item 10 of the PPA audit)."""
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


def _spec(target: str = "tx", bank: str = "pingpong") -> dict:
    return {
        "name": "pp", "target": target,
        "link": {"payload_width": 8, "words": 4, "line_rate_mbps": 50,
                 "coding": "raw", "bit_order": "lsb_first"},
        "tx": {"architecture": "parallel_mux", "bank": bank},
        "rx": {"architecture": "fixed_oversampling", "oversample": 4,
               "align": "external", "clocking": "independent", "ppm": 0,
               "sync_len": 8},
        "tech": {"tx": "sky130", "rx": "sky130"},
        "interface": {"preset": "minimal", "data": "valid_ready",
                      "control": "native", "cdc": "none",
                      "enable_mode": "static"},
        "constraints": {"uncertainty_setup_ns": 0.1, "uncertainty_hold_ns": 0.05},
    }


class TestPingPong(unittest.TestCase):
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
        return r, out

    def test_pingpong_tx_package(self) -> None:
        r, out = self._run(_spec())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        m = json.loads((out / "manifest.json").read_text())
        self.assertEqual(m["design"], "serdes_tx_digital")
        self.assertEqual(m["tx_bank_mode"], "pingpong")
        self.assertEqual(m["params"]["PINGPONG"], 1)
        self.assertIn("serializer_pingpong.sv",
                      (out / "rtl" / "filelist.f").read_text())

    def test_single_bank_is_the_default(self) -> None:
        r, out = self._run(_spec(bank="single"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        m = json.loads((out / "manifest.json").read_text())
        self.assertEqual(m["tx_bank_mode"], "single")
        self.assertEqual(m["params"]["PINGPONG"], 0)

    def test_pingpong_rejected_for_link(self) -> None:
        r, _ = self._run(_spec(target="link"))
        self.assertEqual(r.returncode, 2)
        self.assertIn("only available for target: tx", r.stdout + r.stderr)

    def test_invalid_bank_rejected(self) -> None:
        r, _ = self._run(_spec(bank="triple"))
        self.assertEqual(r.returncode, 2)
        self.assertIn("tx.bank", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
