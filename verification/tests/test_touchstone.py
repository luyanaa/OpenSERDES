"""Touchstone parser, mixed-mode conversion and time-domain channel tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from verification.channel.touchstone import (
    TouchstoneError,
    load_touchstone,
    load_touchstone_channel,
)


def _write_s2p(path: Path, rows: list[tuple[float, complex, complex, complex, complex]]) -> None:
    lines = ["# HZ S RI R 50"]
    for f, s11, s21, s12, s22 in rows:
        lines.append(
            f"{f} {s11.real} {s11.imag} {s21.real} {s21.imag} "
            f"{s12.real} {s12.imag} {s22.real} {s22.imag}"
        )
    path.write_text("\n".join(lines) + "\n")


def _write_s4p(path: Path, freq: list[float], s: np.ndarray) -> None:
    lines = ["# HZ S RI R 50"]
    n = s.shape[1]
    for k, f in enumerate(freq):
        vals = []
        for j in range(n):       # input
            for i in range(n):   # output
                vals += [s[k, i, j].real, s[k, i, j].imag]
        lines.append(str(f) + " " + " ".join(str(v) for v in vals))
    path.write_text("\n".join(lines) + "\n")


class TestTouchstone(unittest.TestCase):
    def test_s2p_asymmetric_orientation_and_dc(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "link.s2p"
            rows = [
                (1e9, 0.1 + 0j, 0.9 + 0j, 0.2 + 0j, 0.05 + 0j),
                (2e9, 0.15 + 0j, 0.8 + 0j, 0.25 + 0j, 0.06 + 0j),
            ]
            _write_s2p(p, rows)
            r = load_touchstone(p)
            self.assertEqual(r.nports, 2)
            self.assertTrue(r.dc_extrapolated)
            # S21 must be 0.9, not the S12 value 0.2.
            self.assertAlmostEqual(abs(r.sdd21[1]), 0.9, places=12)
            self.assertAlmostEqual(abs(r.sdd11[1]), 0.1, places=12)
            self.assertIn("no DC point", " ".join(r.warnings))

    def test_s4p_mixed_mode_port_map(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "diff.s4p"
            freq = [1e9, 2e9]
            s = np.zeros((2, 4, 4), dtype=complex)
            s[:, 2, 0] = 0.9   # rx_p <- tx_p
            s[:, 3, 1] = 0.9   # rx_n <- tx_n
            _write_s4p(p, freq, s)
            r = load_touchstone(p)
            # Sdd21 = 0.5*(S30 - S31 - S40 + S41)
            self.assertAlmostEqual(abs(r.sdd21[1]), 0.9, places=12)
            self.assertAlmostEqual(abs(r.sdd11[1]), 0.0, places=12)

    def test_bad_port_map_and_missing_dc_strict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "link.s2p"
            _write_s2p(p, [(1e9, 0j, 1 + 0j, 1 + 0j, 0j)])
            with self.assertRaises(TouchstoneError):
                load_touchstone(p, require_dc=True)
            with self.assertRaises(TouchstoneError):
                load_touchstone(p, port_map={"tx": 0})

    def test_impulse_channel_preserves_dc_gain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "flat.s4p"
            freq = [0.0, 5e9, 10e9, 20e9]
            s = np.zeros((4, 4, 4), dtype=complex)
            # 6 dB differential through at all frequencies.
            s[:, 2, 0] = 0.5
            s[:, 3, 1] = 0.5
            _write_s4p(p, freq, s)
            ch = load_touchstone_channel(p, sps=32, ui_s=1e-10)
            # DC gain is the sum of the impulse response for a constant input;
            # the finite-band record has ringing, so compare the DC gain rather
            # than a single sample.
            self.assertAlmostEqual(float(ch.h.sum()), 0.5, places=3)
            out = ch.apply(np.ones(4000))
            self.assertTrue(np.all(np.isfinite(out)))
            self.assertLessEqual(float(np.max(np.abs(out))), 0.6)


if __name__ == "__main__":
    unittest.main()
