"""Power evidence / provenance chain tests (item 3 of the PPA audit)."""
from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COLLECT = ROOT / "scripts" / "collect_power.py"
IMPORT = ROOT / "scripts" / "import_ppa.py"
GEN = ROOT / "scripts" / "gen_ip.py"


def _fixture_run(root: Path) -> Path:
    run = root / "run"
    (run / "final").mkdir(parents=True)
    (run / "final" / "metrics.json").write_text(json.dumps({
        "power__internal__total": 0.003,
        "power__switching__total": 0.001,
        "power__leakage__total": 1e-8,
        "power__total": 0.004,
    }))
    (run / "resolved.json").write_text(json.dumps({
        "DEFAULT_CORNER": "nom_tt_025C_1v80", "CLOCK_PERIOD": 20.0,
        "PDK": "sky130A", "STD_CELL_LIBRARY": "sky130_fd_sc_hd",
    }))
    return run


class TestPowerFlow(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.d = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _collect(self, run: Path, record: Path, csv_out: Path | None = None) -> subprocess.CompletedProcess:
        cmd = [sys.executable, str(COLLECT), "--run", str(run), "--out", str(record)]
        if csv_out:
            cmd += ["--csv-out", str(csv_out), "--tech", "sky130",
                    "--scl", "sky130_fd_sc_hd", "--kind", "rx",
                    "--arch", "source_sync", "--rate", "100", "--source", "fixture"]
        return subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=60)

    def test_collect_fails_closed_without_power_metrics(self) -> None:
        run = self.d / "empty_run"
        (run / "final").mkdir(parents=True)
        (run / "final" / "metrics.json").write_text(json.dumps({"foo": 1}))
        p = self._collect(run, self.d / "r.json")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("power keys", p.stdout + p.stderr)

    def test_collect_records_hash_and_activity_caveat(self) -> None:
        run = _fixture_run(self.d)
        record = self.d / "r.json"
        p = self._collect(run, record)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        d = json.loads(record.read_text())
        self.assertEqual(d["kind"], "librelane-power-evidence")
        self.assertEqual(d["power_mw"], 4.0)
        self.assertEqual(d["corner"], "nom_tt_025C_1v80")
        self.assertEqual(d["period_ns"], 20.0)
        self.assertEqual(d["metrics_sha256"],
                         hashlib.sha256((run / "final/metrics.json").read_bytes()).hexdigest())
        self.assertIn("default", d["activity_source"])

    def test_full_chain_is_accepted(self) -> None:
        run = _fixture_run(self.d)
        record, csv_path = self.d / "r.json", self.d / "row.csv"
        self.assertEqual(self._collect(run, record, csv_path).returncode, 0)
        out = self.d / "ppa.yaml"
        p = subprocess.run([sys.executable, str(IMPORT), "--csv", str(csv_path),
                            "--out", str(out), "--replace"], cwd=ROOT,
                           text=True, capture_output=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        import yaml
        d = yaml.safe_load(out.read_text())
        e = d["entries"][0]
        self.assertEqual(e["confidence"], "measured_post_route")
        self.assertTrue(e["provenance"]["verified"])
        self.assertEqual(e["provenance"]["metrics_sha256"],
                         json.loads(record.read_text())["metrics_sha256"])

    def test_unproven_measured_claim_is_rejected(self) -> None:
        csv_path = self.d / "u.csv"
        csv_path.write_text(
            "tech,scl,kind,arch,max_line_rate_mbps,rel_area,rel_power,rel_latency,confidence,source\n"
            "sky130,sky130_fd_sc_hd,rx,source_sync,100,1,1,1,measured_post_route,fake\n")
        p = subprocess.run([sys.executable, str(IMPORT), "--csv", str(csv_path),
                            "--out", str(self.d / "u.yaml"), "--replace"],
                           cwd=ROOT, text=True, capture_output=True, timeout=60)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("provenance", p.stdout + p.stderr)

    def test_tampered_hash_is_rejected(self) -> None:
        run = _fixture_run(self.d)
        record, csv_path = self.d / "r.json", self.d / "row.csv"
        self.assertEqual(self._collect(run, record, csv_path).returncode, 0)
        text = csv_path.read_text().replace(
            hashlib.sha256(record.read_bytes()).hexdigest(), "0" * 64)
        csv_path.write_text(text)
        p = subprocess.run([sys.executable, str(IMPORT), "--csv", str(csv_path),
                            "--out", str(self.d / "t.yaml"), "--replace"],
                           cwd=ROOT, text=True, capture_output=True, timeout=60)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("sha256 mismatch", p.stdout + p.stderr)

    def test_gen_ip_downgrades_handwritten_measured_yaml(self) -> None:
        ppa = self.d / "hand.yaml"
        ppa.write_text(
            "schema: 1\nentries:\n- tech: icsprout55\n  scl: ics55_LLSC_H7CL\n"
            "  kind: rx\n  arch: fixed_oversampling\n  max_line_rate_mbps: 200\n"
            "  metrics: {area: 1.0, power: 1.0, latency: 1.0}\n"
            "  confidence: measured_post_route\n  source: handwritten\n")
        p = subprocess.run([sys.executable, str(GEN), "--spec", "spec/default.yaml",
                            "--check", "--ppa-db", str(ppa)], cwd=ROOT,
                           text=True, capture_output=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        payload = json.loads(p.stdout)
        self.assertEqual(payload["rx_confidence"], "unverified_claim")
        self.assertTrue(any("downgraded" in w for w in payload["warnings"]))


if __name__ == "__main__":
    unittest.main()
