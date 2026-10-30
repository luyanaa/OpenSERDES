"""Generator fail-closed contracts: coding and unproven CDC."""

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


def _base_spec(**link_overrides) -> dict:
    return {
        "name": "contract_test",
        "target": "link",
        "link": {
            "payload_width": 8,
            "words": 4,
            "line_rate_mbps": 25,
            "coding": "raw",
            "bit_order": "lsb_first",
            **link_overrides,
        },
        "tx": {"architecture": "word_streaming"},
        "rx": {
            "architecture": "source_sync",
            "oversample": 1,
            "align": "comma_per_frame",
            "clocking": "source_sync",
            "ppm": 0,
            "sync_pattern": "0xBC",
            "sync_len": 8,
        },
        "tech": {"tx": "sky130", "rx": "sky130"},
        "interface": {
            "preset": "soc",
            "data": "axi4_stream",
            "control": "apb",
            "cdc": "none",
            "enable_mode": "dynamic",
            "user_clock_mhz": 25,
        },
    }


class TestGeneratorContracts(unittest.TestCase):
    def _run(self, spec: dict) -> tuple[int, str]:
        with tempfile.TemporaryDirectory() as tmp:
            spec_path = Path(tmp) / "spec.yaml"
            spec_path.write_text(yaml.safe_dump(spec))
            out = Path(tmp) / "out"
            proc = subprocess.run(
                [sys.executable, str(GEN), "--spec", str(spec_path),
                 "--out", str(out), "--force"],
                cwd=ROOT, text=True, capture_output=True,
            )
            return proc.returncode, proc.stdout + proc.stderr

    def test_non_raw_coding_is_rejected(self) -> None:
        spec = _base_spec(coding="8b10b")
        code, text = self._run(spec)
        self.assertEqual(code, 2)
        self.assertIn("not implemented", text)

    def test_standalone_training_is_fail_closed(self) -> None:
        spec = _base_spec()
        spec["capabilities"] = {"training": "phase_threshold"}
        code, text = self._run(spec)
        self.assertEqual(code, 2)
        self.assertIn("RTL_STANDALONE", text)

    def test_explicit_none_capability_is_the_bypass(self) -> None:
        spec = _base_spec()
        spec["capabilities"] = {
            "training": "none",
            "equalization": "none",
            "termination": "none",
        }
        code, text = self._run(spec)
        self.assertEqual(code, 0, text)

    def test_unproven_cdc_auto_becomes_async(self) -> None:
        spec = _base_spec()
        spec["interface"]["cdc"] = "auto"
        with tempfile.TemporaryDirectory() as tmp:
            spec_path = Path(tmp) / "spec.yaml"
            spec_path.write_text(yaml.safe_dump(spec))
            out = Path(tmp) / "out"
            proc = subprocess.run(
                [sys.executable, str(GEN), "--spec", str(spec_path),
                 "--out", str(out), "--force"],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            manifest = json.loads((out / "manifest.json").read_text())
            self.assertEqual(manifest["interface"]["cdc"], "async_fifo")
            self.assertTrue(any("does not prove" in w for w in manifest["warnings"]))
            caps = manifest["capabilities"]
            self.assertTrue(caps["coding"]["integrated"])
            self.assertTrue(caps["framing"]["integrated"])
            self.assertFalse(caps["training"]["integrated"])
            self.assertFalse(caps["crc"]["integrated"])
            self.assertEqual(caps["training"]["implementation"], "rtl_standalone")

    def test_cdc_none_with_mismatched_user_clock_is_rejected(self) -> None:
        spec = _base_spec()
        spec["interface"]["user_clock_mhz"] = 100  # PHY is 25 Mb/s
        code, text = self._run(spec)
        self.assertEqual(code, 2)
        self.assertIn("differs from its PHY clock", text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
