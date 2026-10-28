"""Capability-gate integration: verify_link.py -> ber.json contract."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "verify_link.py"
PROFILE = ROOT / "verification" / "profiles" / "stressed.yaml"


class TestSignoffConsumesGate(unittest.TestCase):
    def test_collect_signoff_reads_nested_ber_checks(self) -> None:
        import sys as _sys
        _sys.path.insert(0, str(ROOT / "scripts"))
        from collect_signoff import collect  # type: ignore

        with tempfile.TemporaryDirectory() as tmp:
            pkg = Path(tmp)
            reports = pkg / "reports"
            reports.mkdir()
            (pkg / "manifest.json").write_text(
                json.dumps({"design": "serdes_soc_wrapper", "spec_name": "soc"})
            )
            (reports / "ber.json").write_text(json.dumps({
                "ber_target": 1e-3,
                "ber": 5e-4,
                "jtol_pass": True,
                "eye_margin_ui": 0.2,
                "checks": {"ber": 5e-4, "jtol_pass": True, "eye_margin_ui": 0.2},
            }))
            report = collect(pkg, reports)
            self.assertEqual(report["checks"]["ber"], 5e-4)
            self.assertTrue(report["checks"]["jtol_pass"])
            self.assertEqual(report["checks"]["eye_margin_ui"], 0.2)
            self.assertEqual(report["ber_target"], 1e-3)


class TestGateBehavioral(unittest.TestCase):
    def test_skip_rtl_never_claims_capability(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "ber.json"
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), "--profile", str(PROFILE),
                 "--out", str(out), "--skip-rtl", "--eye-bits", "1000"],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            report = json.loads(out.read_text())
            self.assertFalse(report["capability"]["supported"])
            self.assertIn("ber", report["checks"])
            self.assertIn("jtol_pass", report["checks"])
            self.assertIn("eye_margin_ui", report["checks"])


@unittest.skipUnless(shutil.which("iverilog") and shutil.which("vvp"),
                     "iverilog/vvp not installed")
class TestGateRtl(unittest.TestCase):
    def test_quick_gate_passes_and_writes_checks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pkg = Path(tmp) / "pkg"
            pkg.mkdir()
            (pkg / "manifest.json").write_text(json.dumps({
                "design": "serdes_soc_wrapper",
                "line_rate_mbps": 100.0,
                "spec_sha256": "test",
                "params": {"DATA_WIDTH": 8, "WORDS": 4, "SYNC_LEN": 8},
                "interface": {"preset": "soc"},
            }))
            out = Path(tmp) / "ber.json"
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), "--profile", str(PROFILE),
                 "--out", str(out), "--bits", "4000", "--jtol-bits", "300",
                 "--jtol-freqs", "200000", "--jtol-amps", "0.05", "0.1",
                 "--jtol-min-amp-ui", "0.05", "--eye-bits", "2000",
                 "--package", str(pkg)],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            report = json.loads(out.read_text())
            self.assertTrue(report["capability"]["supported"])
            self.assertLessEqual(report["checks"]["ber"], report["ber_target"])
            self.assertTrue(report["checks"]["jtol_pass"])
            self.assertGreater(report["checks"]["eye_margin_ui"], 0.0)
            self.assertFalse(report["capability"]["pvt_validated"])
            self.assertTrue(report["capability"]["binding"]["bound"])
            self.assertTrue((pkg / "reports" / "ber.json").is_file())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
