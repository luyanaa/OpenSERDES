"""Tests for PRBS generators against the RTL recurrence."""

from __future__ import annotations

import unittest

import numpy as np

from verification.patterns.prbs import PRBS_PERIOD, prbs_bits, prbs_words

# Tap pairs copied from rtl/prbs_gen.sv.
_TAPS = {7: (6, 5), 15: (14, 13), 31: (30, 27)}


def _rtl_reference_bits(width: int, n: int, seed: int | None = None) -> np.ndarray:
    """Independent list-based implementation of the RTL recurrence."""
    tap_hi, tap_lo = _TAPS[width]
    state = [1] * width if seed is None else [
        (int(seed) >> i) & 1 for i in range(width)
    ]
    if not any(state):
        raise ValueError("zero seed")
    out = np.empty(n, dtype=np.int8)
    for i in range(n):
        out[i] = state[-1]  # combinatorial output = MSB before shift
        feedback = state[tap_hi] ^ state[tap_lo]
        # RTL: {state[WIDTH-2:0], feedback} -> old bits move one index up,
        # feedback enters at index 0.
        state = [feedback] + state[:-1]
    return out


class TestPRBSBits(unittest.TestCase):
    def test_first_prbs7_bits_match_rtl_derivation(self) -> None:
        bits = prbs_bits(7, 8)
        expected = _rtl_reference_bits(7, 8)
        # The literal documented in the task, independently derived above.
        np.testing.assert_array_equal(expected, np.array([1, 1, 1, 1, 1, 1, 1, 0], dtype=np.int8))
        np.testing.assert_array_equal(bits, expected)

    def test_prbs7_period_and_seed_restoration(self) -> None:
        period = PRBS_PERIOD[7]
        bits = prbs_bits(7, 2 * period)
        np.testing.assert_array_equal(bits[:period], bits[period:])
        # State recurrence returns exactly to the seed after one period and
        # not earlier (minimal period).
        state = (1 << 7) - 1
        mask = (1 << 7) - 1
        first = state
        found = None
        for step in range(1, period + 1):
            tap_hi, tap_lo = _TAPS[7]
            feedback = ((state >> tap_hi) ^ (state >> tap_lo)) & 1
            state = ((state << 1) & mask) | feedback
            if state == first:
                found = step
                break
        self.assertEqual(found, period)

    def test_prbs15_period(self) -> None:
        period = PRBS_PERIOD[15]
        bits = prbs_bits(15, 2 * period)
        np.testing.assert_array_equal(bits[:period], bits[period:])
        state = (1 << 15) - 1
        mask = (1 << 15) - 1
        first = state
        found = None
        for step in range(1, period + 1):
            tap_hi, tap_lo = _TAPS[15]
            feedback = ((state >> tap_hi) ^ (state >> tap_lo)) & 1
            state = ((state << 1) & mask) | feedback
            if state == first:
                found = step
                break
        self.assertEqual(found, period)

    def test_prbs31_matches_reference_for_a_short_prefix(self) -> None:
        # A full 2^31-1 period is not enumerated; verify the tap positions
        # against the independent RTL recurrence over a prefix.
        self.assertEqual(PRBS_PERIOD[31], 2**31 - 1)
        np.testing.assert_array_equal(
            prbs_bits(31, 64), _rtl_reference_bits(31, 64)
        )

    def test_custom_seed_matches_reference(self) -> None:
        for width in (7, 15):
            seed = 0x55 & ((1 << width) - 1)
            np.testing.assert_array_equal(
                prbs_bits(width, 50, seed=seed),
                _rtl_reference_bits(width, 50, seed=seed),
            )

    def test_invalid_width_and_seed(self) -> None:
        for width in (6, 8, 16, 32, 0, -7):
            with self.assertRaises(ValueError):
                prbs_bits(width, 10)
        with self.assertRaises(ValueError):
            prbs_bits(7, 10, seed=0)
        with self.assertRaises(ValueError):
            prbs_bits(7, -1)

    def test_zero_length(self) -> None:
        self.assertEqual(prbs_bits(7, 0).size, 0)
        self.assertEqual(prbs_words(7, 0, 8).size, 0)


class TestPRBSWords(unittest.TestCase):
    def test_lsb_first_word_assembly(self) -> None:
        n_words, word_bits = 17, 8
        bits = prbs_bits(7, n_words * word_bits)
        words = prbs_words(7, n_words, word_bits)
        self.assertEqual(words.shape, (n_words,))
        for j in range(n_words):
            expected = sum(int(bits[j * word_bits + k]) << k for k in range(word_bits))
            self.assertEqual(int(words[j]), expected)

    def test_prbs7_first_word_is_0x7f(self) -> None:
        # First eight bits: 1 1 1 1 1 1 1 0.  LSB-first packing -> 0x7f.
        words = prbs_words(7, 1, 8)
        self.assertEqual(int(words[0]), 0x7F)

    def test_word_bits_validation(self) -> None:
        with self.assertRaises(ValueError):
            prbs_words(7, 1, 0)
        with self.assertRaises(ValueError):
            prbs_words(7, 1, 65)
        with self.assertRaises(ValueError):
            prbs_words(7, -1, 8)


if __name__ == "__main__":
    unittest.main()
