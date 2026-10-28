import unittest

from verification.analog.pdk_mc import success_for, summarize


class TestMcFailClosed(unittest.TestCase):
    def test_mismatch_requires_local_signal(self) -> None:
        self.assertTrue(success_for("mismatch", True, False, True))
        self.assertFalse(success_for("mismatch", True, False, False))
        self.assertFalse(success_for("mismatch", False, False, True))

    def test_global_and_both_require_complete_samples(self) -> None:
        self.assertTrue(success_for("global", True, True, False))
        self.assertFalse(success_for("global", True, False, True))
        self.assertTrue(success_for("both", True, True, True))
        self.assertFalse(success_for("both", True, True, False))
        self.assertFalse(success_for("both", True, False, True))

    def test_invalid_rows_do_not_count_as_samples(self) -> None:
        rows = [
            {"valid": True, "i1_a": 1.0, "i2_a": 1.1,
             "common_a": 1.05, "differential_a": -0.1},
            {"valid": False, "i1_a": None, "i2_a": None},
        ]
        summary = summarize(rows)
        self.assertEqual(summary["n"], 2)
        self.assertEqual(summary["n_valid"], 1)
        self.assertEqual(summary["common"]["mean"], 1.05)


if __name__ == "__main__":
    unittest.main()
