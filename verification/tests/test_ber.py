"""Tests for the ideal slicer, Gaussian-tail BER and JTOL harness."""

from __future__ import annotations

import unittest

import numpy as np
from scipy.stats import norm

from verification.analysis.ber import (
    confidence_interval,
    gaussian_ber,
    required_bits_for_ber,
    statistical_ber,
)
from verification.analysis.jtol import jtol_curve, run_jtol
from verification.channel.channel_impulse import nrz_waveform
from verification.channel.rx_slicer import q_function, sample_and_decide


class TestSlicer(unittest.TestCase):
    def test_q_function_values(self) -> None:
        self.assertAlmostEqual(float(q_function(0.0)), 0.5, places=15)
        self.assertAlmostEqual(float(q_function(1.2815515655)), 0.1, places=9)
        self.assertAlmostEqual(float(q_function(-1.2815515655)), 0.9, places=9)

    def test_sampling_positions_are_floor_and_discard(self) -> None:
        waveform = np.arange(300, dtype=np.float64)
        bits = np.zeros(20, dtype=np.int8)
        decisions, indices = sample_and_decide(
            waveform,
            bits,
            sps=10,
            phase_ui=0.5,
            threshold_v=0.0,
            discard=2,
        )
        expected = np.floor((np.arange(2, 20) + 0.5) * 10).astype(np.int64)
        np.testing.assert_array_equal(indices, expected)
        self.assertEqual(decisions.size, 18)
        np.testing.assert_array_equal(decisions, np.ones(18, dtype=np.int8))

    def test_sampling_positions_clipped(self) -> None:
        waveform = np.array([0.0, 1.0, 2.0])
        bits = np.zeros(5, dtype=np.int8)
        _, indices = sample_and_decide(
            waveform, bits, sps=10, phase_ui=0.99, discard=0
        )
        self.assertTrue(np.all(indices <= 2))
        self.assertTrue(np.all(indices >= 0))
        self.assertEqual(int(indices[-1]), 2)


class TestGaussianBER(unittest.TestCase):
    def _symmetric_record(self) -> tuple[np.ndarray, np.ndarray]:
        bits = np.tile(np.array([0, 1], dtype=np.int8), 500)
        waveform = nrz_waveform(bits, sps=8)  # exact -0.5/+0.5 rails
        return bits, waveform

    def test_monotonic_with_noise_sigma(self) -> None:
        bits, waveform = self._symmetric_record()
        sigmas = [0.01, 0.02, 0.05, 0.10, 0.20, 0.50]
        bers = []
        for sigma in sigmas:
            result = gaussian_ber(
                waveform,
                bits,
                sps=8,
                phases=[0.5],
                threshold_v=0.0,
                noise_rms_v=sigma,
                discard=0,
            )
            self.assertEqual(result["ber"].shape, (1,))
            bers.append(float(result["ber"][0]))
            # Symmetric zero-ISI rails: predicted BER is Q(0.5 / sigma).
            self.assertAlmostEqual(
                bers[-1], float(q_function(0.5 / sigma)), places=12
            )
        self.assertTrue(np.all(np.diff(bers) > 0.0), msg=str(bers))

    def test_large_noise_approaches_half_at_threshold(self) -> None:
        bits, waveform = self._symmetric_record()
        result = gaussian_ber(
            waveform,
            bits,
            sps=8,
            phases=[0.5],
            threshold_v=0.0,
            noise_rms_v=100.0,
            discard=0,
        )
        self.assertAlmostEqual(float(result["ber"][0]), 0.5, delta=0.02)

    def test_threshold_grid_shape_and_orientation(self) -> None:
        bits, waveform = self._symmetric_record()
        result = gaussian_ber(
            waveform,
            bits,
            sps=8,
            phases=[0.25, 0.75],
            noise_rms_v=0.1,
            discard=0,
            thresholds=np.array([-1.0, -0.5, 0.0, 0.5, 1.0]),
        )
        self.assertEqual(result["ber"].shape, (2, 5))
        self.assertEqual(result["mu0"].shape, (2,))
        self.assertTrue(np.all(result["n0"] > 0))
        self.assertTrue(np.all(result["n1"] > 0))

    def test_alias(self) -> None:
        self.assertIs(statistical_ber, gaussian_ber)


class TestConfidenceAndRequiredBits(unittest.TestCase):
    def test_zero_error_rule_of_three_interval(self) -> None:
        lo, hi = confidence_interval(0, 1000)
        self.assertEqual(lo, 0.0)
        self.assertAlmostEqual(hi, 0.003)
        lo2, hi2 = confidence_interval(0, 1000, level=0.99)
        self.assertEqual(lo2, 0.0)
        self.assertGreater(hi2, hi)

    def test_wilson_interval_for_errors(self) -> None:
        lo, hi = confidence_interval(10, 1000)
        self.assertLess(lo, 0.01)
        self.assertGreater(hi, 0.01)
        self.assertGreaterEqual(lo, 0.0)
        self.assertLessEqual(hi, 1.0)
        lo_all, hi_all = confidence_interval(1000, 1000)
        self.assertGreaterEqual(lo_all, 0.0)
        self.assertLessEqual(hi_all, 1.0)

    def test_required_bits(self) -> None:
        self.assertEqual(required_bits_for_ber(1e-3), 3000)
        self.assertGreaterEqual(required_bits_for_ber(1e-12), 3e12)
        self.assertGreater(
            required_bits_for_ber(1e-12, confidence=0.99),
            required_bits_for_ber(1e-12),
        )
        self.assertEqual(required_bits_for_ber(0.0), float("inf"))
        with self.assertRaises(ValueError):
            required_bits_for_ber(-1e-9)


class TestJTOL(unittest.TestCase):
    def test_run_and_curve_last_passing(self) -> None:
        def simulate(stimulus: dict) -> dict:
            return {
                "ber": float(stimulus["sj_peak_ui"]) * 1e-3,
                "lock_losses": 0,
                "bit_slips": 0,
            }

        rows = run_jtol(
            simulate,
            sj_frequencies_hz=[100.0, 200.0],
            sj_amplitudes_ui=[0.1, 0.2, 0.3],
            base_stress={"rate_mbps": 100.0},
        )
        self.assertEqual(len(rows), 6)
        self.assertEqual(rows[0]["sj_freq_hz"], 100.0)
        self.assertEqual(rows[0]["sj_amplitude_ui"], 0.1)
        self.assertAlmostEqual(rows[0]["sj_peak_to_peak_ui"], 0.2)
        self.assertAlmostEqual(rows[0]["ber"], 1e-4)

        curve = jtol_curve(rows, ber_limit=2e-4, lock_loss_limit=0)
        self.assertEqual(len(curve), 2)
        for point in curve:
            self.assertEqual(point["status"], "last_passing")
            self.assertEqual(point["max_passing_amplitude_ui"], 0.2)
            self.assertAlmostEqual(point["max_passing_peak_to_peak_ui"], 0.4)

        all_pass = jtol_curve(rows, ber_limit=1.0)
        self.assertTrue(all(point["status"] == "all_tested_pass" for point in all_pass))
        self.assertTrue(all(point["max_passing_amplitude_ui"] == 0.3 for point in all_pass))

        none_pass = jtol_curve(rows, ber_limit=1e-9)
        self.assertTrue(all(point["status"] == "no_pass" for point in none_pass))
        self.assertTrue(all(point["max_passing_amplitude_ui"] is None for point in none_pass))

    def test_missing_result_key_raises(self) -> None:
        with self.assertRaises(KeyError):
            run_jtol(
                lambda stimulus: {"ber": 0.0, "lock_losses": 0},
                [100.0],
                [0.1],
            )

    def test_json_serializable(self) -> None:
        import json

        rows = run_jtol(
            lambda stimulus: {"ber": 1e-6, "lock_losses": 0, "bit_slips": 0},
            [100.0],
            [0.1],
        )
        json.dumps(rows)
        json.dumps(jtol_curve(rows))


if __name__ == "__main__":
    unittest.main()
