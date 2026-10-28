"""RTL CDR JTOL harness tests (skipped when iverilog is unavailable)."""

from __future__ import annotations

import shutil
import unittest

from verification.rtl.runner import run_rtl_jtol


@unittest.skipUnless(shutil.which("iverilog") and shutil.which("vvp"),
                     "iverilog/vvp not installed")
class TestRtlJtol(unittest.TestCase):
    def test_clean_edges_recover_without_errors(self) -> None:
        result = run_rtl_jtol(nbits=300, rj_rms_ui=0.0, sj_peak_ui=0.0,
                              sj_freq_hz=0.0, dcd_peak_ui=0.0, ppm=0.0)
        self.assertTrue(result.locked)
        self.assertEqual(result.errors, 0)
        self.assertEqual(result.ber, 0.0)
        self.assertGreater(result.recovered, 0)
        self.assertEqual(result.lock_losses, 0)

    def test_ppm_offset_is_tracked(self) -> None:
        result = run_rtl_jtol(nbits=300, ppm=300.0)
        self.assertTrue(result.locked)
        self.assertEqual(result.errors, 0)
        self.assertEqual(result.bit_slips, 0)

    def test_sinusoidal_jitter_degrades_ber(self) -> None:
        result = run_rtl_jtol(nbits=300, sj_peak_ui=0.2, sj_freq_hz=2e6)
        self.assertTrue(result.locked)
        self.assertGreater(result.ber, 0.0)

    def test_full_rx_chain_clean_and_noisy(self) -> None:
        clean = run_rtl_jtol(nbits=300, chain="rx")
        self.assertTrue(clean.locked)
        self.assertEqual(clean.errors, 0)
        noisy = run_rtl_jtol(nbits=300, chain="rx", noise_rms_v=0.12)
        self.assertTrue(noisy.locked)
        self.assertGreaterEqual(noisy.ber, clean.ber)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
