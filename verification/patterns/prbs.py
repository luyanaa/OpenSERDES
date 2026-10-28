"""PRBS7/15/31 generators that match ``rtl/prbs_gen.sv`` exactly.

RTL convention
--------------
``rtl/prbs_gen.sv`` implements a Fibonacci LFSR whose state register is shifted
numerically left (the old MSB is shifted out, the feedback bit enters at bit
0), and whose serial output is the MSB of the state *before* the shift::

    output   = state[WIDTH-1]
    feedback = state[tap_hi] ^ state[tap_lo]
    state    = ((state << 1) & mask) | feedback

with ``SEED = '1`` (all ones) by default.  The tap pairs are

===========  =====================
width        taps (state indices)
===========  =====================
7            6 ^ 5
15           14 ^ 13
31           30 ^ 27
===========  =====================

Because the shift direction and output position are both explicit, the first
eight PRBS7 bits from an all-ones seed are ``[1, 1, 1, 1, 1, 1, 1, 0]``.  The
implementation below deliberately uses the same integer recurrence rather than
a precomputed table, so a future RTL change can be cross-checked directly.

Word packing
------------
:func:`prbs_words` packs the *contiguous serial stream* into integer words
**LSB-first**: bit ``k`` of word ``j`` is serial bit ``j * word_bits + k``.
Equivalently, the first serial bit of a word is its least-significant bit.  This
matches the repository serializer's ``LSB_FIRST=1`` mode; use a bit-reversal if
a different word convention is needed elsewhere.
"""

from __future__ import annotations

import numpy as np

#: Full periods of the maximal-length sequences used by the RTL.
PRBS_PERIOD = {7: 127, 15: 32767, 31: 2**31 - 1}

#: RTL tap pairs (state bit indices), keyed by register width.
_TAPS = {7: (6, 5), 15: (14, 13), 31: (30, 27)}

__all__ = ["PRBS_PERIOD", "prbs_bits", "prbs_words"]


def _validate_width(width: int) -> int:
    try:
        width = int(width)
    except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
        raise ValueError(f"PRBS width must be 7, 15 or 31; got {width!r}") from exc
    if width not in PRBS_PERIOD:
        raise ValueError(f"PRBS width must be 7, 15 or 31; got {width!r}")
    return width


def _step(state: int, width: int) -> int:
    """Advance one clock using the RTL recurrence (state is pre-shift)."""
    tap_hi, tap_lo = _TAPS[width]
    feedback = ((state >> tap_hi) ^ (state >> tap_lo)) & 1
    return ((state << 1) & ((1 << width) - 1)) | feedback


def _initial_state(width: int, seed: int | None) -> int:
    mask = (1 << width) - 1
    state = mask if seed is None else int(seed) & mask
    if state == 0:
        # All-zero is the lock-up state of a Fibonacci LFSR.  The RTL would
        # emit zeros forever, which is not a PRBS; fail loudly instead.
        raise ValueError("PRBS seed must be non-zero (all-zero is the LFSR lock-up state)")
    return state


def prbs_bits(width: int, n: int, seed: int | None = None) -> np.ndarray:
    """Return ``n`` serial PRBS bits for PRBS-``width``.

    Parameters
    ----------
    width:
        One of 7, 15, 31.
    n:
        Number of serial bits.  Must be non-negative.
    seed:
        Initial LFSR state.  ``None`` means the RTL reset value (all ones).
        The value is masked to ``width`` bits.

    Returns
    -------
    numpy.ndarray
        ``int8`` array of length ``n`` containing 0/1 in serial order.
    """
    width = _validate_width(width)
    n = int(n)
    if n < 0:
        raise ValueError(f"n must be non-negative; got {n}")
    state = _initial_state(width, seed)
    out = np.empty(n, dtype=np.int8)
    msb = width - 1
    for i in range(n):
        out[i] = (state >> msb) & 1
        state = _step(state, width)
    return out


def prbs_words(
    width: int, n_words: int, word_bits: int, seed: int | None = None
) -> np.ndarray:
    """Return PRBS words packed LSB-first from the contiguous serial stream.

    Word ``j`` contains serial bits ``j*word_bits ... (j+1)*word_bits-1`` with
    the first serial bit stored in bit 0 (LSB) of the returned integer.  This is
    the natural packing for the repository's ``LSB_FIRST=1`` serializer.

    Parameters
    ----------
    width:
        PRBS width: 7, 15 or 31.
    n_words:
        Number of words to generate.
    word_bits:
        Width of each packed word, ``1 <= word_bits <= 64``.
    seed:
        Initial LFSR state (``None`` = all ones).

    Returns
    -------
    numpy.ndarray
        ``uint64`` array of length ``n_words``.
    """
    width = _validate_width(width)
    n_words = int(n_words)
    word_bits = int(word_bits)
    if n_words < 0:
        raise ValueError(f"n_words must be non-negative; got {n_words}")
    if not 1 <= word_bits <= 64:
        raise ValueError(f"word_bits must be in [1, 64]; got {word_bits}")

    bits = prbs_bits(width, n_words * word_bits, seed=seed).astype(np.uint64)
    # weights[k] = 2**k, so bit k of the word is serial bit k (LSB-first).
    weights = (np.uint64(1) << np.arange(word_bits, dtype=np.uint64))
    matrix = bits.reshape(n_words, word_bits) if n_words else bits.reshape(0, word_bits)
    # Sum in uint64; for word_bits == 64 the MSB weight is 2**63, which is
    # representable.  numpy's shift for uint64 is well-defined for k <= 63.
    return (matrix * weights).sum(axis=1, dtype=np.uint64)
