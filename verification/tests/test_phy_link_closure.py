import json
import tempfile
import unittest
from pathlib import Path

from verification.analog.phy_link_closure import collect


class TestPhyLinkClosure(unittest.TestCase):
    """The closure collector must fail closed: no run -> UNVERIFIED, and a
    tampered metric -> UNVERIFIED, never a fabricated SIGNOFF_ELIGIBLE."""

    def test_missing_run_is_unverified(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            rec = collect(Path(td) / "no_such_run", Path(td) / "out")
            self.assertEqual(rec["status"], "UNVERIFIED")
            self.assertIn("metrics_unreadable", rec["problems"])
            self.assertFalse(rec["checks"]["gds_present"])

    def test_clean_run_is_signoff_eligible(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            run = Path(td) / "run"
            (run / "final" / "gds").mkdir(parents=True)
            (run / "final" / "spef" / "max").mkdir(parents=True)
            (run / "final" / "spice").mkdir(parents=True)
            (run / "final" / "gds" / "serdes_analog_link.gds").write_bytes(b"GDS")
            (run / "final" / "spef" / "max" /
             "serdes_analog_link.max.spef").write_text("*SPEF\n")
            (run / "final" / "spice" /
             "serdes_analog_link.spice").write_text(".subckt x a b\n.ends\n")
            metrics = {k: 0 for k in (
                "design__xor_difference__count", "magic__drc_error__count",
                "klayout__drc_error__count",
                "design__lvs_unmatched_device__count",
                "design__lvs_unmatched_net__count",
                "design__lvs_unmatched_pin__count", "design__lvs_error__count",
                "timing__setup_vio__count", "timing__hold_vio__count",
                "route__drc_errors", "route__antenna_violation__count",
                "design__power_grid_violation__count", "flow__errors__count")}
            metrics["timing__setup__ws"] = 12.9
            metrics["timing__hold__ws"] = 0.05
            (run / "final" / "metrics.json").write_text(json.dumps(metrics))

            rec = collect(run, Path(td) / "out")
            self.assertEqual(rec["status"], "SIGNOFF_ELIGIBLE")
            self.assertEqual(rec["problems"], [])
            self.assertTrue(all(rec["checks"].values()))
            self.assertEqual(rec["views"]["gds"]["status"], "available")
            self.assertIn("evidence_hash", rec)

            # A single tampered metric must flip it back to UNVERIFIED.
            metrics["magic__drc_error__count"] = 7
            (run / "final" / "metrics.json").write_text(json.dumps(metrics))
            rec2 = collect(run, Path(td) / "out2")
            self.assertEqual(rec2["status"], "UNVERIFIED")
            self.assertIn("magic_drc_zero", rec2["problems"])


if __name__ == "__main__":
    unittest.main()
