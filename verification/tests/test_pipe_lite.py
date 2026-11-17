"""PIPE-Lite digital compatibility shell (PIPE_DIGITAL_COMPAT)."""
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
    spec = yaml.safe_load((ROOT / "spec/pipe_lite.yaml").read_text())
    for key, value in overrides.items():
        if key in ("width", "words", "coding"):
            spec["link"][key if key != "width" else "payload_width"] = value
        elif key == "target":
            spec["target"] = value
        elif key == "preset":
            spec["interface"]["preset"] = value
        else:
            spec[key] = value
    return spec


class TestPipeLite(unittest.TestCase):
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

    def test_pipe_lite_package(self) -> None:
        r, m, sdc, files = self._run(_spec())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(m["design"], "serdes_pipe_lite_top")
        self.assertEqual(m["interface_class"], "PIPE_DIGITAL_COMPAT")
        self.assertEqual(m["clocking"]["ports"], ["clk"])
        self.assertIn("set WORD_DIV 20", sdc)
        self.assertIn("create_generated_clock -name tx_usr_clk", sdc)
        self.assertIn("create_generated_clock -name rx_rec_clk", sdc)
        self.assertIn("serdes_pipe_lite_top.sv", files)
        self.assertIn("pipe_elastic_buffer.sv", files)
        self.assertIn("encoder_8b10b.sv", files)

    def test_rejects_32bit(self) -> None:
        r, _, _, _ = self._run(_spec(width=32))
        self.assertEqual(r.returncode, 2)
        self.assertIn("16-bit PIPE", r.stdout + r.stderr)

    def test_rejects_raw_coding(self) -> None:
        r, _, _, _ = self._run(_spec(coding="raw"))
        self.assertEqual(r.returncode, 2)
        self.assertIn("8b10b", r.stdout + r.stderr)

    def test_rejects_link_target(self) -> None:
        r, _, _, _ = self._run(_spec(target="link"))
        self.assertEqual(r.returncode, 2)

    def test_rejects_multi_word_bus(self) -> None:
        r, _, _, _ = self._run(_spec(words=4))
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
