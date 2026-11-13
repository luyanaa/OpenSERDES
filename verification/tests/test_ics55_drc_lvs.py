"""ICsprout55 repaired KLayout DRC/LVS cell gate."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PDK = Path("/grid/icsprout55-pdk")
RUNNER = ROOT / "verification" / "analog" / "ics55_drc_lvs.py"


@unittest.skipUnless(
    PDK.is_dir() and shutil.which("klayout")
    and (PDK / "libs.tech/lvs/run_lvs.py").is_file(),
    "ICsprout55 PDK or klayout not installed")
class TestIcs55DrcLvs(unittest.TestCase):
    def test_released_cell_lvs_passes_and_drc_report_is_written(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "evidence.json"
            proc = subprocess.run(
                [sys.executable, str(RUNNER), "--pdk", str(PDK),
                 "--out", str(out), "--workdir", str(Path(tmp) / "work")],
                cwd=ROOT, text=True, capture_output=True, timeout=1800)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            data = json.loads(out.read_text())
            self.assertEqual(data["lvs"]["compare"], "PASS")
            self.assertEqual(data["top"], "INVX1H7R")
            self.assertIsNotNone(data["drc"]["items"])
            self.assertGreater(data["drc"]["items"], 0)
            self.assertTrue(Path(data["drc"]["report"]).is_file())
            self.assertTrue(Path(data["lvs"]["report"]).is_file())


if __name__ == "__main__":
    unittest.main()
