"""ICsprout55 repaired KLayout DRC/LVS cell gate."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PDK = Path("/grid/icsprout55-pdk")
RUNNER = ROOT / "verification" / "analog" / "ics55_drc_lvs.py"


def _drc_deck_complete(pdk: Path) -> bool:
    """The PDK deck assembles every rule module it references."""
    deck = pdk / "libs.tech/drc/ics55_drc.drc"
    if not deck.is_file():
        return False
    refs = re.findall(r"'(rule_decks/[^']+)'", deck.read_text())
    return bool(refs) and all((deck.parent / ref).is_file() for ref in refs)


def _pdk_clean(pdk: Path) -> bool:
    """The PDK deck is committed, not a work-in-progress edit."""
    proc = subprocess.run(["git", "-C", str(pdk), "status", "--porcelain"],
                          text=True, capture_output=True, timeout=30)
    return proc.returncode == 0 and not proc.stdout.strip()


@unittest.skipUnless(
    PDK.is_dir() and shutil.which("klayout")
    and (PDK / "libs.tech/lvs/run_lvs.py").is_file()
    and _drc_deck_complete(PDK) and _pdk_clean(PDK),
    "ICsprout55 PDK is missing, dirty (repair in progress), or incomplete")
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
