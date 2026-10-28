"""Tests for the TIE jitter model."""

from __future__ import annotations

import unittest

import numpy as np

from verification.channel.jitter import build_tie, sinusoidal_tie


class TestJitter(unittest.TestCase):
    def setUp(self) -> None:
        self.bits = np.array([0, 1, 0, 1, 0, 1, 0, 1, 0, 1], dtype=np.int8)
        self.ui_s = 10e-9  # 100 Mb/s

    def test_zero_stress_gives_zero_tie(self) -> None:
        tie, meta = build_tie(
            self.bits,
            self.ui_s,
            0.0,
            0.0,
            0.0,
            0.0,
            rng=np.random.default_rng(1),
        )
        np.testing.assert_array_equal(tie, np.zeros(self.bits.size + 1))
        self.assertEqual(meta["sj_peak_ui"], 0.0)
        self.assertEqual(meta["sj_peak_to_peak_ui"], 0.0)

    def test_sj_peak_and_peak_to_peak(self) -> None:
        # f * UI = 1/4 makes the sampled sine hit exactly +A, 0, -A, 0 ...
        peak = 0.10
        freq = 1.0 / (4.0 * self.ui_s)
        tie, meta = build_tie(
            np.ones(200, dtype=np.int8),
            self.ui_s,
            0.0,
            peak,
            freq,
            0.0,
            rng=np.random.default_rng(2),
        )
        tie_ui = tie / self.ui_s
        self.assertAlmostEqual(float(np.max(np.abs(tie_ui))), peak, places=12)
        self.assertAlmostEqual(float(np.ptp(tie_ui)), 2.0 * peak, places=12)
        self.assertEqual(meta["sj_peak_ui"], peak)
        self.assertEqual(meta["sj_peak_to_peak_ui"], 2.0 * peak)
        self.assertAlmostEqual(meta["sj_observed_peak_ui"], peak, places=12)
        self.assertAlmostEqual(meta["sj_observed_peak_to_peak_ui"], 2.0 * peak, places=12)

    def test_dcd_sign_follows_transitions(self) -> None:
        dcd = 0.1
        tie, meta = build_tie(
            self.bits,
            self.ui_s,
            0.0,
            0.0,
            0.0,
            dcd,
            rng=np.random.default_rng(3),
        )
        tie_ui = tie / self.ui_s
        # Boundary 1 is 0->1, boundary 2 is 1->0, etc.  Boundary 0 and the
        # final boundary (after the last bit) have no preceding transition.
        expected = np.zeros(self.bits.size + 1)
        transitions = self.bits[1:].astype(int) - self.bits[:-1].astype(int)
        expected[1 : self.bits.size] = dcd * np.sign(transitions)
        np.testing.assert_allclose(tie_ui, expected, atol=1e-15)
        self.assertEqual(meta["dcd_peak_ui"], dcd)

    def test_rng_determinism(self) -> None:
        args = (self.bits, self.ui_s, 0.05, 0.02, 100e6, 0.01)
        tie_a, _ = build_tie(*args, rng=np.random.default_rng(1234))
        tie_b, _ = build_tie(*args, rng=np.random.default_rng(1234))
        np.testing.assert_array_equal(tie_a, tie_b)
        tie_c, _ = build_tie(*args, rng=np.random.default_rng(5678))
        self.assertFalse(np.array_equal(tie_a, tie_c))

    def test_ddj_and_wander_injection(self) -> None:
        n = self.bits.size + 1
        ddj = np.linspace(-0.01, 0.01, n)
        wander = np.linspace(0.02, -0.02, n)
        tie, meta = build_tie(
            self.bits,
            self.ui_s,
            0.0,
            0.0,
            0.0,
            0.0,
            rng=np.random.default_rng(4),
            ddj_ui=ddj,
            wander_ui=wander,
        )
        np.testing.assert_allclose(tie / self.ui_s, ddj + wander, atol=1e-15)
        self.assertTrue(meta["ddj_applied"])
        self.assertTrue(meta["wander_applied"])

    def test_nonmonotonic_raises_with_index(self) -> None:
        bits = np.ones(20, dtype=np.int8)
        ddj = np.zeros(bits.size + 1)
        ddj[10] = -1.1
        with self.assertRaisesRegex(ValueError, r"nonmonotonic.*10"):
            build_tie(
                bits,
                self.ui_s,
                0.0,
                0.0,
                0.0,
                0.0,
                rng=np.random.default_rng(5),
                ddj_ui=ddj,
            )

    def test_invalid_sj_frequency(self) -> None:
        with self.assertRaises(ValueError):
            build_tie(
                self.bits,
                self.ui_s,
                0.0,
                0.1,
                0.0,
                0.0,
                rng=np.random.default_rng(6),
            )

    def test_sinusoidal_tie_helper(self) -> None:
        ui_s = 1e-9
        tie_ui = sinusoidal_tie(101, ui_s, 0.2, 1.0 / (4.0 * ui_s))
        self.assertAlmostEqual(float(np.max(np.abs(tie_ui))), 0.2, places=12)
        zero = sinusoidal_tie(11, ui_s, 0.2, 0.0)
        np.testing.assert_array_equal(zero, np.zeros(11))


if __name__ == "__main__":
    unittest.main()
