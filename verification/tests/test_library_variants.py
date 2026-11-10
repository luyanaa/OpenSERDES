"""Selectable measured libraries / SCL variants (item 9 of the PPA audit)."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
GEN = ROOT / "scripts" / "gen_ip.py"


def _load_gen():
    spec = importlib.util.spec_from_file_location("gen_ip_mod", GEN)
    mod = importlib.util.module_from_spec(spec)
    # Register before exec so dataclasses can resolve their module namespace.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


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

    def test_ez130_without_pdk_reports_missing(self) -> None:
        # CI has no /grid PDK tree.  Generation must still succeed and report
        # pdk_lib_files_present=false, not fail and not claim the libs exist.
        spec = yaml.safe_load((ROOT / "spec/ihp_ez130.yaml").read_text())
        r, m = self._run(spec)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(m["tech"]["scl"], "ez130_8t_v1p1")
        gen = _load_gen()
        tech = gen.load_tech("ihp-sg13g2", "ez130_8t_v1p1")
        expected = gen.lib_files_present(gen.expand(tech.corners))
        self.assertEqual(m["tech"]["pdk_lib_files_present"], expected)
        if not expected:
            self.assertFalse(m["tech"]["pdk_lib_files_present"])

    def test_lib_files_present_with_fixture(self) -> None:
        # A private Liberty fixture must resolve to true; a missing path to
        # false.  This is the "path resolution" half that does not need a PDK.
        gen = _load_gen()
        lib = self.d / "fix.lib"
        lib.write_text("library (fix) { }\n")
        self.assertTrue(gen.lib_files_present({"nom_tt": [str(lib)]}))
        self.assertFalse(gen.lib_files_present(
            {"nom_tt": [str(self.d / "absent.lib")]}))
        self.assertFalse(gen.lib_files_present({}))

    def test_ez130_variant_with_fixture_resolves(self) -> None:
        # A fixture tech profile whose corners point at temp .lib files must
        # report pdk_lib_files_present=true through the real loader.
        gen = _load_gen()
        tmp = self.d
        (tmp / "lib").mkdir()
        lib = tmp / "lib" / "ez130_tt.lib"
        lib.write_text("library (ez130) { }\n")
        techdir = tmp / "tech" / "fixturepdk"
        techdir.mkdir(parents=True)
        (techdir / "tech.yaml").write_text(yaml.safe_dump({
            "name": "fixturepdk", "pdk": "fixturepdk", "scl": "fix_sc",
            "corners": {"nom_tt": [str(lib)]},
            "limits": {"class": "MS", "default_period_ns": 20.0},
        }))
        old_root = gen.ROOT
        try:
            gen.ROOT = tmp
            tech = gen.load_tech("fixturepdk", None)
            self.assertTrue(gen.lib_files_present(gen.expand(tech.corners)))
        finally:
            gen.ROOT = old_root

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
