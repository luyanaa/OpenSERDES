"""Raw PCS preset: flat word bus, word clock and rx_slide bitslip."""
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


def _spec(**overrides) -> dict:
    spec = yaml.safe_load((ROOT / "spec/raw_pcs.yaml").read_text())
    for key, value in overrides.items():
        if key in ("width", "words", "coding"):
            spec["link"][key if key != "width" else "payload_width"] = value
            if key == "width":
                # The comma is a full word in the raw PCS.
                spec["rx"]["sync_len"] = value
        elif key == "target":
            spec["target"] = value
        elif key == "preset":
            spec["interface"]["preset"] = value
        else:
            spec[key] = value
    return spec


class TestRawPcs(unittest.TestCase):
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
                           text=True, capture_output=True, timeout=180)
        if r.returncode != 0:
            return r, None, None, None
        m = json.loads((out / "manifest.json").read_text())
        sdc = (out / "constraints" / "pre_pnr.sdc").read_text()
        files = (out / "rtl" / "filelist.f").read_text()
        return r, m, sdc, files

    def test_raw_pcs_package(self) -> None:
        r, m, sdc, files = self._run(_spec())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(m["design"], "serdes_raw_pcs_top")
        self.assertEqual(m["clocking"]["ports"], ["clk"])
        self.assertEqual(m["params"]["DATA_WIDTH"], 32)
        self.assertNotIn("WORDS", m["params"])
        self.assertIn("create_generated_clock -name tx_usr_clk", sdc)
        self.assertIn("create_generated_clock -name rx_rec_clk", sdc)
        self.assertIn("divide_by $WORD_DIV", sdc)
        self.assertIn("set WORD_DIV 32", sdc)
        self.assertIn("serdes_raw_pcs_top.sv", files)

    def test_all_flat_widths(self) -> None:
        for width in (16, 20, 32, 40, 64):
            r, m, sdc, _ = self._run(_spec(width=width))
            self.assertEqual(r.returncode, 0, f"width {width}: {r.stdout}{r.stderr}")
            self.assertEqual(m["params"]["DATA_WIDTH"], width)
            self.assertIn(f"set WORD_DIV {width}", sdc)

    def test_rejects_odd_width(self) -> None:
        r, _, _, _ = self._run(_spec(width=24))
        self.assertEqual(r.returncode, 2)
        self.assertIn("16, 20, 32, 40, 64", r.stdout + r.stderr)

    def test_rejects_link_target(self) -> None:
        r, _, _, _ = self._run(_spec(target="link"))
        self.assertEqual(r.returncode, 2)
        self.assertIn("target: rx or tx", r.stdout + r.stderr)

    def test_rejects_multi_word_bus(self) -> None:
        r, _, _, _ = self._run(_spec(words=4))
        self.assertEqual(r.returncode, 2)
        self.assertIn("link.words: 1", r.stdout + r.stderr)

    def test_rejects_coded_stream(self) -> None:
        r, _, _, _ = self._run(_spec(coding="8b10b"))
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
