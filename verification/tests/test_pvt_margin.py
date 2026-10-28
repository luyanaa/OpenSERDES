import math
import unittest

from verification.cli import load_profile
from scripts.pvt_margin import margin_for_row, ui_ns_from_rate


class TestPvtMarginUnits(unittest.TestCase):
    def test_ui_units_for_three_rates(self) -> None:
        self.assertEqual(ui_ns_from_rate(100.0), 10.0)
        self.assertEqual(ui_ns_from_rate(1000.0), 1.0)
        self.assertEqual(ui_ns_from_rate(10.0), 100.0)

    def test_tau_ui_matches_hand_calculation(self) -> None:
        base = load_profile("verification/profiles/clean.yaml")
        row = {"corner": "tt", "vdd_v": 1.8, "temperature_c": 25.0}
        for rate, ui_ns in ((100.0, 10.0), (1000.0, 1.0), (10.0, 100.0)):
            profile = dict(base)
            profile["rate_mbps"] = rate
            profile["ui_s"] = 1.0 / (rate * 1e6)
            row["tx_rise_ns"] = 0.5
            expected = (0.5 / math.log(4.0)) / ui_ns
            out = margin_for_row(row, profile, bits_n=400)
            self.assertTrue(out["valid"])
            self.assertAlmostEqual(out["tau_ui"], expected, places=14)
            self.assertAlmostEqual(out["tx_tau_ui"], expected, places=14)
            self.assertEqual(out["channel_tau_ui"], profile["channel"]["tau_ui"])

    def test_nonfinite_rise_is_invalid(self) -> None:
        base = load_profile("verification/profiles/clean.yaml")
        for rise in (None, float("nan"), float("inf"), 0.0, -1.0):
            row = {"corner": "tt", "tx_rise_ns": rise}
            self.assertFalse(margin_for_row(row, base, bits_n=100)["valid"])

    def test_bad_rate_rejected(self) -> None:
        for bad in (0.0, -1.0, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                ui_ns_from_rate(bad)


if __name__ == "__main__":
    unittest.main()
