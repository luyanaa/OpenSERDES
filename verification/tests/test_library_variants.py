"""Selectable measured libraries / SCL variants (item 9 of the PPA audit)."""
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


class TestLibraryVariants(unittest.TestCase):
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

    def test_ez130_variant_generates_with_resolved_libs(self) -> None:
        spec = yaml.safe_load((ROOT / "spec/ihp_ez130.yaml").read_text())
        r, m = self._run(spec)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(m["tech"]["scl"], "ez130_8t_v1p1")
        self.assertEqual(m["tech"]["native_dff"], "DFFRQX2")
        self.assertTrue(m["tech"]["pdk_lib_files_present"])

    def test_unknown_scl_is_rejected(self) -> None:
        spec = yaml.safe_load((ROOT / "spec/ihp_ez130.yaml").read_text())
        spec["tech"] = {"tx": {"name": "ihp-sg13g2", "scl": "nope"},
                        "rx": {"name": "ihp-sg13g2", "scl": "nope"}}
        r, _ = self._run(spec)
        self.assertEqual(r.returncode, 2)
        self.assertIn("does not define scl", r.stdout + r.stderr)

    def test_link_with_different_endpoint_scl_is_rejected(self) -> None:
        spec = yaml.safe_load((ROOT / "spec/ihp_ez130.yaml").read_text())
        spec["target"] = "link"
        spec["tech"] = {"tx": {"name": "icsprout55", "scl": "ics55_LLSC_H7CL"},
                        "rx": {"name": "icsprout55", "scl": "ics55_LLSC_H7CR"}}
        r, _ = self._run(spec)
        self.assertEqual(r.returncode, 2)
        text = r.stdout + r.stderr
        self.assertIn("same tech/scl", text)
        self.assertIn("target: tx", text)

    def test_all_icsprout_variants_still_accepted(self) -> None:
        for scl in ("ics55_LLSC_H7CH", "ics55_LLSC_H7CL", "ics55_LLSC_H7CR"):
            spec = yaml.safe_load((ROOT / "spec/tx_stream.yaml").read_text())
            spec["tech"] = {"tx": {"name": "icsprout55", "scl": scl},
                            "rx": {"name": "icsprout55", "scl": scl}}
            r, m = self._run(spec)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(m["tech"]["scl"], scl)


if __name__ == "__main__":
    unittest.main()
