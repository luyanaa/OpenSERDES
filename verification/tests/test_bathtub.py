"""Tests for empirical bathtub scans and zero-error upper bounds."""

from __future__ import annotations

import unittest

import numpy as np

from verification.analysis.bathtub import phase_scan, threshold_scan
from verification.analysis.eye import eye_metrics, fold_eye
from verification.channel.channel_impulse import (
    RCReflectionChannel,
    load_touchstone_sdd21,
    nrz_waveform,
)
from verification.channel.jitter import build_tie
from verification.patterns.prbs import prbs_bits

UI_S = 10e-9  # 100 Mb/s
SPS = 16


def _make_record(
    n_bits: int = 4000,
    *,
    rj_rms_ui: float = 0.0,
    sj_peak_ui: float = 0.0,
    sj_freq_hz: float = 0.0,
    dcd_peak_ui: float = 0.0,
    noise_rms_v: float = 0.0,
    seed: int = 42,
) -> tuple[np.ndarray, np.ndarray]:
    bits = prbs_bits(7, n_bits)
    rng = np.random.default_rng(seed)
    tie, _ = build_tie(
        bits,
        UI_S,
        rj_rms_ui,
        sj_peak_ui,
        sj_freq_hz,
        dcd_peak_ui,
        rng=rng,
    )
    waveform = nrz_waveform(bits, SPS, tie_ui=tie / UI_S)
    waveform = RCReflectionChannel().apply(waveform, SPS)
    if noise_rms_v > 0.0:
        waveform = waveform + rng.normal(0.0, noise_rms_v, size=waveform.shape)
    return bits, waveform


class TestBathtub(unittest.TestCase):
    def test_errors_decrease_toward_best_phase(self) -> None:
        bits, waveform = _make_record(
            rj_rms_ui=0.01,
            sj_peak_ui=0.03,
            sj_freq_hz=1.0 / (4.0 * UI_S),
            dcd_peak_ui=0.02,
            noise_rms_v=0.20,
        )
        phases = np.linspace(0.05, 0.50, 10)
        scan = phase_scan(bits, waveform, SPS, phases=phases, threshold_v=0.0, discard=100)
        errors = scan["errors"]
        self.assertGreater(int(errors[0]), int(errors[-1]))
        self.assertGreater(int(errors[0]), 5 * int(errors[-1]))
        # Moving toward the best phase (0.5 UI for this record) should not
        # increase the measured error count at any step in this deterministic
        # seeded test.
        self.assertTrue(np.all(np.diff(errors) <= 0), msg=f"errors={errors.tolist()}")
        self.assertEqual(scan["best_phase"], 0.5)

    def test_zero_error_rule_of_three(self) -> None:
        bits, waveform = _make_record()  # clean, noiseless, no jitter
        scan = phase_scan(
            bits,
            waveform,
            SPS,
            phases=np.array([0.3, 0.4, 0.5, 0.6, 0.7]),
            threshold_v=0.0,
            discard=100,
        )
        n = bits.size - 100
        self.assertEqual(scan["n_bits"], n)
        np.testing.assert_array_equal(scan["errors"], np.zeros(5, dtype=np.int64))
        np.testing.assert_array_equal(scan["zero_error"], np.ones(5, dtype=bool))
        np.testing.assert_allclose(scan["upper95"], np.full(5, 3.0 / n))
        self.assertAlmostEqual(scan["best_upper95"], 3.0 / n)
        self.assertAlmostEqual(scan["best_ber"], 0.0)

    def test_nonzero_errors_have_no_zero_error_bound(self) -> None:
        bits, waveform = _make_record(noise_rms_v=0.25)
        scan = phase_scan(
            bits,
            waveform,
            SPS,
            phases=np.array([0.05]),
            threshold_v=0.0,
            discard=100,
        )
        self.assertGreater(int(scan["errors"][0]), 0)
        self.assertFalse(bool(scan["zero_error"][0]))
        self.assertFalse(bool(scan["upper95_applicable"][0]))
        self.assertTrue(np.isnan(scan["upper95"][0]))
        self.assertAlmostEqual(scan["best_ber"], scan["errors"][0] / scan["n_bits"])

    def test_threshold_scan_shape_and_zero_region(self) -> None:
        bits, waveform = _make_record()
        thresholds = np.linspace(-0.4, 0.4, 5)
        scan = threshold_scan(
            bits,
            waveform,
            SPS,
            phases=np.array([0.45, 0.5]),
            thresholds=thresholds,
            discard=100,
        )
        self.assertEqual(scan["ber"].shape, (2, 5))
        self.assertEqual(scan["errors"].shape, (2, 5))
        self.assertEqual(scan["upper95"].shape, (2, 5))
        np.testing.assert_array_equal(scan["errors"][:, 1:4], np.zeros((2, 3)))
        np.testing.assert_allclose(scan["upper95"][:, 1:4], np.full((2, 3), 3.0 / scan["n_bits"]))
        self.assertTrue(np.all(scan["errors"][:, 0] > 0))
        self.assertTrue(np.all(scan["errors"][:, 4] > 0))


class TestChannel(unittest.TestCase):
    def test_rc_only_step_is_monotonic_and_settles(self) -> None:
        sps = 16
        channel = RCReflectionChannel(reflections=())
        step = np.ones(500 * sps, dtype=np.float64)
        response = channel.apply(step, sps)
        self.assertTrue(np.all(np.isfinite(response)))
        self.assertTrue(np.all(np.diff(response) >= -1e-15))
        self.assertAlmostEqual(float(response[-1]), 1.0, places=9)
        self.assertGreater(float(response[-1]), float(response[0]))

    def test_default_channel_step_dc_gain_and_no_nan(self) -> None:
        sps = 16
        channel = RCReflectionChannel()
        step = np.ones(1000 * sps, dtype=np.float64)
        response = channel.apply(step, sps)
        self.assertTrue(np.all(np.isfinite(response)))
        self.assertAlmostEqual(float(response[-1]), 1.0, delta=1e-3)
        self.assertLessEqual(float(np.max(np.abs(response))), 1.3)

    def test_default_channel_impulse_settles(self) -> None:
        sps = 16
        channel = RCReflectionChannel()
        impulse = np.zeros(500 * sps, dtype=np.float64)
        impulse[0] = 1.0
        response = channel.apply(impulse, sps)
        self.assertTrue(np.all(np.isfinite(response)))
        self.assertLess(abs(float(response[-1])), 1e-12)

    def test_touchstone_missing_file_raises_clearly(self) -> None:
        with self.assertRaises(FileNotFoundError):
            load_touchstone_sdd21("not_a_channel.s2p")

    def test_eye_fold_shape_and_metrics(self) -> None:
        bits = prbs_bits(7, 4000)
        sps = 16
        waveform = RCReflectionChannel().apply(nrz_waveform(bits, sps), sps)
        folded = fold_eye(waveform, sps, ui_window=2)
        self.assertEqual(folded.ndim, 2)
        self.assertEqual(folded.shape[1], 2 * sps)
        metrics = eye_metrics(folded, threshold_v=0.0, sps=sps)
        self.assertGreater(metrics["eye_height_v"], 0.0)
        self.assertGreater(metrics["eye_width_ui"], 0.0)
        self.assertLess(metrics["eye_width_ui"], 2.0)


if __name__ == "__main__":
    unittest.main()
