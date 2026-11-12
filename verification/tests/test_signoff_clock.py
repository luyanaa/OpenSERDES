"""Physical signoff also asserts the sample-clock evidence tier."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from collect_signoff import collect  # noqa: E402
from qualify import evaluate  # noqa: E402


def _write_reports(pkg: Path) -> Path:
    reports = pkg / "reports"
    reports.mkdir(parents=True)
    (reports / "rtl_functional.json").write_text(json.dumps(
        {"checks": {"lint": True, "sim": True, "synth": True}}))
    for name, key, value in (("pre_pnr_setup.rpt", "setup", "0.12"),
                             ("pre_pnr_hold.rpt", "hold", "0.01"),
                             ("post_route_setup.rpt", "setup", "0.04"),
                             ("post_route_hold.rpt", "hold", "0.008")):
        (reports / name).write_text(f"{key} WNS: {value}\n")
    (reports / "drc.rpt").write_text("DRC clean: 0 violations\n")
    (reports / "lvs.rpt").write_text("Circuits match uniquely\n")
    (reports / "antenna.rpt").write_text("Antenna clean: 0 violations\n")
    (reports / "cdc.rpt").write_text("CDC clean: 0 violations\n")
    (reports / "ber.json").write_text(json.dumps({
        "ber_target": 1e-12, "ber": 1e-13,
        "jtol_pass": True, "eye_margin_ui": 0.2,
    }))
    return reports


def _manifest(arch: str, evidence: dict | None) -> dict:
    manifest = {"design": "x", "spec_name": "x",
                "architecture": {"rx": {"name": arch}}}
    if evidence is not None:
        manifest["clocking"] = {"evidence": evidence}
    return manifest


class TestSignoffClockEvidence(unittest.TestCase):
    def _collect(self, arch: str, evidence: dict | None,
                 metrics: dict | None = None) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            pkg = Path(tmp) / "pkg"
            pkg.mkdir()
            (pkg / "manifest.json").write_text(json.dumps(_manifest(arch, evidence)))
            reports = _write_reports(pkg)
            if metrics is not None:
                (reports / "metrics.json").write_text(json.dumps(metrics))
            return collect(pkg, reports)

    def test_physical_tier_allows_physical_signoff(self) -> None:
        report = self._collect("bang_bang_oversampling", {
            "modeled": True, "tier": "physical_signoff",
            "timing_closed_mhz": 45.45, "physical_signoff_mhz": 45.45,
        })
        self.assertTrue(report["checks"]["clock_evidence_physical_signoff"])
        result = evaluate(report["checks"], report["ber_target"], "PHYSICAL_SIGNOFF")
        self.assertTrue(result["requested_pass"], result["missing_for_requested"])

    def test_timing_only_tier_blocks_physical_signoff(self) -> None:
        report = self._collect("bang_bang_oversampling", {
            "modeled": True, "tier": "timing_closed",
            "timing_closed_mhz": 45.45, "physical_signoff_mhz": None,
        })
        self.assertFalse(report["checks"]["clock_evidence_physical_signoff"])
        result = evaluate(report["checks"], report["ber_target"], "PHYSICAL_SIGNOFF")
        self.assertFalse(result["requested_pass"])
        self.assertEqual(result["achieved"], "POST_ROUTE_TIMING")
        self.assertIn("clock_evidence_physical_signoff",
                      result["missing_for_requested"])

    def test_old_bang_bang_package_fails_closed(self) -> None:
        report = self._collect("bang_bang_oversampling", None)
        self.assertNotIn("clock_evidence_physical_signoff", report["checks"])
        result = evaluate(report["checks"], report["ber_target"], "PHYSICAL_SIGNOFF")
        self.assertFalse(result["requested_pass"])
        self.assertIn("clock_evidence_physical_signoff",
                      result["missing_for_requested"])

    def test_source_sync_has_no_clock_closure_model(self) -> None:
        report = self._collect("source_sync", None)
        self.assertTrue(report["checks"]["clock_evidence_physical_signoff"])
        result = evaluate(report["checks"], report["ber_target"], "PHYSICAL_SIGNOFF")
        self.assertTrue(result["requested_pass"], result["missing_for_requested"])

    def test_metrics_cannot_override_clock_evidence(self) -> None:
        report = self._collect("bang_bang_oversampling", {
            "modeled": True, "tier": "timing_closed"}, metrics={
            "checks": {"clock_evidence_physical_signoff": True}})
        self.assertFalse(report["checks"]["clock_evidence_physical_signoff"])
        self.assertIn("clock_evidence_physical_signoff",
                      report["manual_overrides"])


if __name__ == "__main__":
    unittest.main()
