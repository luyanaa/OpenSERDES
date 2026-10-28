import json
import tempfile
import unittest
from pathlib import Path

from scripts.collect_signoff import collect, parse_clean_report


class TestSignoffParser(unittest.TestCase):
    def test_exact_violation_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "drc.rpt"
            p.write_text("DRC result\n10 violations\n")
            value, _ = parse_clean_report(p, "drc")
            self.assertFalse(value)
            p.write_text("DRC result\n0 violations\n")
            value, _ = parse_clean_report(p, "drc")
            self.assertTrue(value)
            p.write_text("DRC result\nviolations: 0\n")
            value, _ = parse_clean_report(p, "drc")
            self.assertTrue(value)

    def test_not_clean_is_not_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "drc.rpt"
            p.write_text("DRC is not clean\n")
            value, _ = parse_clean_report(p, "drc")
            self.assertFalse(value)

    def test_generic_clean_is_ambiguous(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "drc.rpt"
            p.write_text("clean\n")
            value, _ = parse_clean_report(p, "drc")
            self.assertIsNone(value)

    def test_tool_nonzero_exit_rejects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "antenna.rpt"
            p.write_text("Antenna clean: 0 violations\nexit code: 2\n")
            value, _ = parse_clean_report(p, "antenna")
            self.assertFalse(value)

    def test_metrics_cannot_override_tool_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pkg = Path(tmp) / "pkg"
            reports = pkg / "reports"
            reports.mkdir(parents=True)
            (pkg / "manifest.json").write_text(json.dumps({"design": "x"}))
            (reports / "drc.rpt").write_text("DRC result\n10 violations\n")
            (reports / "metrics.json").write_text(json.dumps({
                "checks": {"drc_clean": True, "supplementary_score": 7}
            }))
            report = collect(pkg, reports)
            self.assertFalse(report["checks"]["drc_clean"])
            self.assertEqual(report["checks"]["supplementary_score"], 7)
            self.assertIn("drc_clean", report["manual_overrides"])

    def test_stale_gds_hash_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pkg = Path(tmp) / "pkg"
            reports = pkg / "reports"
            reports.mkdir(parents=True)
            (pkg / "manifest.json").write_text(json.dumps({"design": "x"}))
            (pkg / "layout.gds").write_bytes(b"new-layout")
            (reports / "drc.rpt").write_text(
                "DRC clean: 0 violations\ngds_sha256: deadbeef\n"
            )
            report = collect(pkg, reports)
            self.assertNotIn("drc_clean", report["checks"])
            self.assertTrue(report["evidence"]["drc_clean"]["stale_input"])

    def test_lvs_requires_explicit_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "lvs.rpt"
            p.write_text("LVS clean\n")
            value, _ = parse_clean_report(p, "lvs")
            self.assertIsNone(value)
            p.write_text("Circuits match uniquely\n")
            value, _ = parse_clean_report(p, "lvs")
            self.assertTrue(value)
            p.write_text("Circuits do not match\n")
            value, _ = parse_clean_report(p, "lvs")
            self.assertFalse(value)


if __name__ == "__main__":
    unittest.main()
