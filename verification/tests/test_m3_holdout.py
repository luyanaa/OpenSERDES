"""Tests for the M3 held-out phase/validation helper."""

from __future__ import annotations

import unittest

from verification.analog.m3_holdout import prbs7


class TestM3Holdout(unittest.TestCase):
    def test_prbs7_is_deterministic_and_seed_sensitive(self) -> None:
        a = prbs7(64, 0x5D)
        b = prbs7(64, 0x5D)
        c = prbs7(64, 0x33)
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertEqual(len(a), 64)
        self.assertEqual(set(a), {0, 1})

    def test_prbs7_is_not_all_constant(self) -> None:
        bits = prbs7(64, 0x5D)
        self.assertGreater(sum(bits), 0)
        self.assertLess(sum(bits), len(bits))


if __name__ == "__main__":
    unittest.main()
