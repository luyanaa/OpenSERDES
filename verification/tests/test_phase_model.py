"""Reference jitter / phase model: Python side and RTL equivalence.

The important test is ``test_rtl_matches_reference``: it compiles
``tb/tb_phase_model.sv`` with the same integer parameters as the Python model,
runs it, and requires every edge's TIE to match.  That is what keeps the RTL
twin and this module from drifting apart.
"""
from __future__ import annotations

import json
import math
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from verification.channel.phase_model import (
    SIN_LUT,
    XorShift16,
    edge_tie_ps,
    irwin_hall_units,
    jitter_stats,
    phase_inc,
    reference_table,
    sin_from_phase,
    tj_at_ber,
    write_vector,
)

ROOT = Path(__file__).resolve().parents[2]
IVERILOG = shutil.which("iverilog")
VVP = shutil.which("vvp")


class TestPrngAndSurrogates(unittest.TestCase):
    def test_xorshift_matches_reference_sequence(self) -> None:
        rng = XorShift16(1)
        seq = [rng.step() for _ in range(5)]
        # Recompute from the definition: x ^= x<<7; x ^= x>>9; x ^= x<<8.
        x = 1
        expect = []
        for _ in range(5):
            x ^= (x << 7) & 0xFFFF
            x ^= x >> 9
            x ^= (x << 8) & 0xFFFF
            x &= 0xFFFF
            expect.append(x)
        self.assertEqual(seq, expect)

    def test_zero_seed_is_remapped(self) -> None:
        self.assertEqual(XorShift16(0).state, 0xACE1)
        self.assertNotEqual(XorShift16(0).step(), 0)

    def test_irwin_hall_is_zero_mean_unit_variance(self) -> None:
        rng = XorShift16(7)
        vals = [irwin_hall_units(rng) / 65536.0 for _ in range(20000)]
        mean = sum(vals) / len(vals)
        var = sum((v - mean) ** 2 for v in vals) / (len(vals) - 1)
        self.assertLess(abs(mean), 0.02)
        # 12 uniforms summed: variance is exactly 1 before quantisation.
        self.assertAlmostEqual(var, 1.0, delta=0.06)

    def test_sine_table_is_a_quarter_wave(self) -> None:
        self.assertEqual(SIN_LUT[0], 0)
        self.assertEqual(SIN_LUT[32], 32768)      # 90 deg = +1.0
        self.assertEqual(SIN_LUT[63], 1608)
        self.assertEqual(len(SIN_LUT), 64)
        # Monotonic over the first quadrant.
        self.assertTrue(all(SIN_LUT[i] <= SIN_LUT[i + 1] for i in range(31)))

    def test_sine_quadrants_are_antisymmetric(self) -> None:
        inc = phase_inc(5_000_000, 10000)
        # k and k + half cycle must be exact negatives.
        half = 1 << 31
        for k in (0, 1, 7, 33, 64, 129):
            self.assertEqual(sin_from_phase(k * inc),
                             -sin_from_phase(k * inc + half))


class TestPhaseIncrement(unittest.TestCase):
    def test_increment_fits_and_is_exact(self) -> None:
        # 2^32 * 5e6 * 1e4 / 1e12 = 214748364.8 -> 214748364
        self.assertEqual(phase_inc(5_000_000, 10000), 214_748_364)

    def test_increment_does_not_overflow(self) -> None:
        # The naive product is 2.1e20, larger than a signed 64-bit integer;
        # the factored form must still be right.
        for freq, ui in ((1, 1), (1000, 37), (12_345_679, 100_000),
                         (250_000_000, 1_000_000)):
            exact = ((1 << 32) * freq * ui) // 1_000_000_000_000
            self.assertEqual(phase_inc(freq, ui), exact)


class TestEdgeTie(unittest.TestCase):
    def test_zero_stress_is_zero(self) -> None:
        self.assertEqual(edge_tie_ps(16, 10000), [0] * 16)

    def test_ppm_ramp_sign_and_slope(self) -> None:
        tie = edge_tie_ps(11, 10000, ppm=100)
        # -ppm * k * UI / 1e6, truncated: -1 ps per edge at 100 ppm / 10 ns.
        self.assertEqual(tie[0], 0)
        self.assertEqual(tie[10], -10)

    def test_sj_peak_bounds(self) -> None:
        # 16384/65536 = 0.25 UI peak; the table's own resolution loses ~4% near
        # the peak, so require 0.23 .. 0.25 UI (2300 .. 2500 ps at 10 ns UI).
        tie = edge_tie_ps(512, 10000, sj_peak_ui_q=16384,
                          sj_freq_hz=5_000_000)
        peak = max(abs(v) for v in tie)
        self.assertLessEqual(peak, 2500)
        self.assertGreater(peak, 2300)

    def test_dcd_sign_follows_transitions(self) -> None:
        pattern = [0, 1, 1, 0, 1, 0]
        tie = edge_tie_ps(6, 10000, dcd_peak_ui_q=6554,
                          dcd_pattern=pattern)
        # 0.1 UI = 1000 ps; sign of the transition into each edge.
        self.assertEqual(tie, [0, 1000, 0, -1000, 1000, -1000])

    def test_rj_is_deterministic_for_a_seed(self) -> None:
        a = edge_tie_ps(64, 10000, rj_rms_ui_q=1311, seed=44257)
        b = edge_tie_ps(64, 10000, rj_rms_ui_q=1311, seed=44257)
        c = edge_tie_ps(64, 10000, rj_rms_ui_q=1311, seed=1234)
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)


class TestStatistics(unittest.TestCase):
    def test_rj_estimator_recovers_the_amplitude(self) -> None:
        sigma_ui = 0.02
        tie = edge_tie_ps(4000, 10000, rj_rms_ui_q=round(sigma_ui * 65536))
        stats = jitter_stats(tie)
        # Difference-based estimator; 4000 edges of an Irwin-Hall surrogate.
        self.assertAlmostEqual(stats["rj_rms_ps"] / 10000.0, sigma_ui,
                               delta=0.004)

    def test_tj_at_ber_matches_dual_dirac(self) -> None:
        out = tj_at_ber(1e-12, 10000, 0.02, 0.10)
        # Qinv(1e-12) is 7.0345 for a two-sided BER definition.
        self.assertAlmostEqual(out["qinv"], 7.0345, places=3)
        self.assertAlmostEqual(out["tj_ui"], 0.10 + 2 * 7.0345 * 0.02,
                               places=4)
        self.assertGreater(out["tj_ps"], 0.0)


@unittest.skipIf(not (IVERILOG and VVP), "iverilog/vvp not available")
class TestRtlEquivalence(unittest.TestCase):
    """The RTL twin must reproduce the Python vector bit for bit."""

    CASES = (
        # (name, extra python args, extra verilog defines)
        ("zero", [], []),
        ("rj", ["--rj-rms-ui-q", "1311", "--seed", "44257"],
         ["-DTB_RJ=1311", "-DTB_SEED=44257"]),
        ("sj", ["--sj-peak-ui-q", "16384", "--sj-freq-hz", "5000000"],
         ["-DTB_SJ=16384", "-DTB_SJ_FREQ=5000000"]),
        ("ppm", ["--ppm", "150"], ["-DTB_PPM=150"]),
        ("dcd", ["--dcd-peak-ui-q", "6554", "--dcd-alternate"],
         ["-DTB_DCD=6554", "-DTB_DCD_PATTERN=1"]),
        ("mixed", ["--rj-rms-ui-q", "6554", "--sj-peak-ui-q", "4915",
                   "--sj-freq-hz", "2000000", "--ppm", "120"],
         ["-DTB_RJ=6554", "-DTB_SJ=4915", "-DTB_SJ_FREQ=2000000",
          "-DTB_PPM=120"]),
    )
    EDGES = 48

    def _run(self, name: str, py_args: list[str],
             defines: list[str], tmp: Path) -> tuple[str, str]:
        vec = tmp / f"{name}.hex"
        proc = subprocess.run(
            [str(Path(shutil.which("python3") or "python3")), "-m",
             "verification.channel.phase_model",
             "--edges", str(self.EDGES), "--ui-ps", "10000",
             "--out", str(vec), *py_args],
            cwd=ROOT, text=True, capture_output=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

        exe = tmp / f"tb_phase_{name}"
        build = subprocess.run(
            [IVERILOG, "-g2012", *defines,
             f"-DTB_N_EDGES={self.EDGES}", "-o", str(exe),
             "tb/tb_phase_model.sv", "tb/jitter_clock.sv"],
            cwd=ROOT, text=True, capture_output=True, timeout=180)
        self.assertEqual(build.returncode, 0,
                         build.stdout + build.stderr)
        run = subprocess.run([VVP, str(exe), f"+vector={vec}"],
                             cwd=ROOT, text=True, capture_output=True,
                             timeout=180)
        return run.stdout + run.stderr, str(vec)

    def test_rtl_matches_reference(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            for name, py_args, defines in self.CASES:
                with self.subTest(case=name):
                    out, vec = self._run(name, py_args, defines, tmp)
                    self.assertIn("PASS: tb_phase_model", out,
                                  f"{name}: {out[-800:]}")

    def test_written_vector_has_one_word_per_edge(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            dest = Path(tmpdir) / "v.hex"
            table = reference_table(17, 10000, rj_rms_ui_q=1000)
            write_vector(dest, table)
            words = dest.read_text().split()
            self.assertEqual(len(words), 17)
            for w in words:
                self.assertEqual(len(w), 8)


class TestJsonExport(unittest.TestCase):
    def test_reference_table_is_serializable(self) -> None:
        table = reference_table(32, 10000, ppm=100, rj_rms_ui_q=1311,
                                sj_peak_ui_q=8192, sj_freq_hz=2_000_000)
        text = json.dumps(table)
        back = json.loads(text)
        self.assertEqual(back["n_edges"], 32)
        self.assertEqual(len(back["tie_ps"]), 32)
        self.assertIn("tj_at_ber", back)
        self.assertIn("1e-12", back["tj_at_ber"])


if __name__ == "__main__":
    unittest.main()
