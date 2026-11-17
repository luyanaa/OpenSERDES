"""Reference jitter / phase model: the Python twin of ``tb/jitter_clock.sv``.

Why this exists
---------------
The RTL loops need a reference for what a jittered sampling clock *is*, and the
Python analysis side must produce the same numbers the RTL sees.  Two
independent approximations would drift apart, so both sides implement one
documented generator with **identical integer arithmetic**:

* a 16-bit xorshift PRNG (``x ^= x<<7; x ^= x>>9; x ^= x<<8``, zero seed
  remapped to ``0xACE1``);
* an Irwin-Hall surrogate for the Gaussian: the sum of 12 uniform draws
  ``x/65536`` minus 6.  Mean 0, variance exactly 1 -- no Box-Muller, no shared
  transcendental, so an HDL block and this module agree bit for bit;
* a 64-entry Q1.15 quarter-wave sine table (``SIN_LUT[32] = 32768 = 1.0``, not
  ``math.sin``), selected by a 32-bit phase accumulator;
* truncating integer division for the ppm ramp and arithmetic right shifts for
  the scaled products, matching SystemVerilog signed arithmetic.

``edge_tie_ps`` therefore returns exactly the sequence ``tb/jitter_clock.sv``
produces, and ``tb/tb_phase_model.sv`` checks that claim against a vector file
this module writes.

Conventions
-----------
* One "edge" is one rising edge of the sampling clock, i.e. one UI at the
  sampler.  ``n_edges`` edges span ``n_edges`` UI.
* TIE is in **ps** and applies to the edge instant: ``t_k = k*UI + tie_k``.
  Positive TIE is later.
* ``ppm`` is the frequency offset of the sampler relative to the data: +100 ppm
  means the sampling clock is fast, so the term is ``-ppm*k*UI/1e6``.
* ``sj_peak_ui`` is the **peak** (0-to-peak) amplitude; peak-to-peak is 2x.
  ``sj_freq_hz`` is absolute, sampled at *nominal* edge times.
* ``rj_rms_ui`` is the per-edge standard deviation of the white term.
* ``dcd_peak_ui`` applies with the sign of the data transition, and is zero
  where the data does not transition (there is no physical edge there).

Limits (do not overclaim)
-------------------------
* This is a *reference for the digital loops*, not a channel: no ISI, no
  reflection, no CTLE/DFE, no SSC modulation profile.
* The Gaussian is an Irwin-Hall **surrogate**: bounded at +-6 sigma with a
  slightly different tail than a true Gaussian.  ``tj_at_ber`` is a dual-Dirac
  budget built on that assumption; it is not a measured BER.
* The sine is a 64-entry Q1.15 table, so the sinusoid is quantised to about
  1/32768 of full scale near the peak (the table's own resolution, declared
  here rather than hidden).
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Iterable

__all__ = [
    "SIN_LUT",
    "XorShift16",
    "irwin_hall_units",
    "sin_from_phase",
    "phase_inc",
    "edge_tie_ps",
    "jitter_stats",
    "tj_at_ber",
    "reference_table",
    "write_vector",
]

MASK16 = 0xFFFF
MASK32 = 0xFFFF_FFFF
N_UNIFORM = 12

# Q1.16 quarter-wave sine, identical to the `sin_lut` function in
# tb/jitter_clock.sv.  64 entries cover 0 .. 90 degrees.
SIN_LUT: tuple[int, ...] = (
    0, 1608, 3212, 4808, 6393, 7962, 9512, 11039,
    12540, 14010, 15447, 16846, 18205, 19520, 20788, 22006,
    23170, 24279, 25330, 26320, 27246, 28106, 28900, 29622,
    30274, 30853, 31357, 31786, 32138, 32413, 32610, 32729,
    32768, 32729, 32610, 32413, 32138, 31786, 31357, 30853,
    30274, 29622, 28900, 28106, 27246, 26320, 25330, 24279,
    23170, 22006, 20788, 19520, 18205, 16846, 15447, 14010,
    12540, 11039, 9512, 7962, 6393, 4808, 3212, 1608,
)


def _trunc_div(num: int, den: int) -> int:
    """Integer division truncated toward zero (SystemVerilog ``/``).

    Python's ``//`` floors, which differs for negative numerators; the RTL uses
    signed division, so the reference has to match.
    """
    q = abs(num) // abs(den)
    return q if (num >= 0) == (den >= 0) else -q


class XorShift16:
    """16-bit xorshift, the RTL-twin generator.

    ``step`` mirrors ``x ^= x<<7; x ^= x>>9; x ^= x<<8`` masked to 16 bits and
    returns the **new** state, which is what the RTL accumulates.  A zero seed
    is remapped because xorshift is stuck at zero.
    """

    __slots__ = ("state",)

    def __init__(self, seed: int) -> None:
        s = int(seed) & MASK16
        self.state = s if s else 0xACE1

    def step(self) -> int:
        x = self.state
        x ^= (x << 7) & MASK16
        x ^= x >> 9
        x ^= (x << 8) & MASK16
        self.state = x & MASK16
        return self.state


def irwin_hall_units(rng: XorShift16, n: int = N_UNIFORM) -> int:
    """Sum of ``n`` draws in units of 1/65536, minus ``n/2`` full scale.

    For ``n = 12`` the result is in units of one sigma
    (``sum/65536 - 6``), which is what ``gauss`` is in the RTL.
    """
    total = 0
    for _ in range(n):
        total += rng.step()
    return total - (n // 2) * 65536


def sin_from_phase(phase: int) -> int:
    """32-bit phase accumulator -> signed Q1.16 sine.

    Quadrant from bits 31/30, quarter-wave index from bits 29:24.
    ``phase`` is truncated to 32 bits first, like the RTL register.
    """
    p = phase & MASK32
    q = (p >> 24) & 0x3F
    if not (p >> 31):
        return SIN_LUT[63 - q] if (p >> 30) & 1 else SIN_LUT[q]
    return -SIN_LUT[63 - q] if (p >> 30) & 1 else -SIN_LUT[q]


def phase_inc(freq_hz: int, ui_ps: int) -> int:
    """``trunc(2^32 * f * UI / 1e12)``, the RTL phase increment.

    Factored because ``2^32 * f * UI`` overflows a 64-bit intermediate for
    realistic values (f in MHz, UI in ns).  With
    ``2^32 / 1e12 = 2^20 / 244140625``::

        n   = f*UI
        inc = (n // D) * 2^20 + ((n % D) * 2^20) // D,   D = 244140625

    Exact for every ``f*UI <= 1e12``, i.e. every increment that fits in 32 bits.
    """
    n = int(freq_hz) * int(ui_ps)
    d, k = 244_140_625, 1 << 20
    q, r = divmod(n, d)
    return q * k + (r * k) // d


def edge_tie_ps(
    n_edges: int,
    ui_ps: int,
    *,
    ppm: int = 0,
    sj_peak_ui_q: int = 0,
    sj_freq_hz: int = 0,
    sj_frac_bits: int = 16,
    rj_rms_ui_q: int = 0,
    rj_frac_bits: int = 16,
    seed: int = 0xACE1,
    dcd_peak_ui_q: int = 0,
    dcd_frac_bits: int = 16,
    dcd_pattern: Iterable[int] | None = None,
) -> list[int]:
    """Per-edge TIE in ps, bit-identical to ``tb/jitter_clock.sv``.

    ``dcd_pattern`` is an optional 0/1 sequence; DCD takes the sign of the
    transition into that edge and is zero where the data does not change.
    """
    if n_edges < 1:
        raise ValueError(f"n_edges must be >= 1; got {n_edges}")
    if ui_ps <= 0:
        raise ValueError(f"ui_ps must be > 0; got {ui_ps}")
    if sj_peak_ui_q and not sj_freq_hz:
        raise ValueError("sj_freq_hz must be > 0 when the SJ amplitude is set")

    rng = XorShift16(seed)
    inc = phase_inc(sj_freq_hz, ui_ps)
    pattern = list(dcd_pattern) if dcd_pattern is not None else []
    prev_bit = 0

    out: list[int] = []
    for k in range(n_edges):
        # 12 draws, then the surrogate.  The RTL accumulates the *next* state,
        # so the first draw is step() applied to the seed.
        gauss = irwin_hall_units(rng)
        sin_val = sin_from_phase(k * inc)

        # DCD sign from the transition into this edge.
        if dcd_peak_ui_q and 1 <= k < len(pattern):
            sign = 1 if pattern[k] > pattern[k - 1] else (
                -1 if pattern[k] < pattern[k - 1] else 0)
        else:
            sign = 0
        prev_bit = pattern[k] if k < len(pattern) else prev_bit

        ppm_term = _trunc_div(-ppm * k * ui_ps, 1_000_000)
        # sin_val is Q1.15 (SIN_LUT[32] = 32768 = 1.0).
        sj_term = (sj_peak_ui_q * ui_ps * sin_val) >> (sj_frac_bits + 15)
        rj_term = (rj_rms_ui_q * ui_ps * gauss) >> (rj_frac_bits + 16)
        dcd_term = ((dcd_peak_ui_q * ui_ps) >> dcd_frac_bits) * sign
        out.append(ppm_term + sj_term + rj_term + dcd_term)
    return out


def jitter_stats(tie_ps: list[int]) -> dict[str, float]:
    """Measured summary of a TIE vector, in ps.

    The white part is estimated from the first difference, which cancels the
    locally-linear deterministic terms (ppm ramp, low-frequency sinusoid)::

        rj_rms = sqrt(var(diff(TIE)) / 2)
    """
    if len(tie_ps) < 3:
        raise ValueError("need at least 3 edges for a jitter summary")
    diffs = [tie_ps[k + 1] - tie_ps[k] for k in range(len(tie_ps) - 1)]
    mean_diff = sum(diffs) / len(diffs)
    var = sum((d - mean_diff) ** 2 for d in diffs) / (len(diffs) - 1)
    return {
        "edges": float(len(tie_ps)),
        "mean_ps": sum(tie_ps) / len(tie_ps),
        "peak_ps": float(max(abs(v) for v in tie_ps)),
        "p2p_ps": float(max(tie_ps) - min(tie_ps)),
        "rj_rms_ps": math.sqrt(var / 2.0),
    }


def _erfcinv(y: float) -> float:
    """Inverse complementary error function by bisection (dependency-free)."""
    if not (0.0 < y < 2.0):
        raise ValueError(f"erfcinv argument must be in (0, 2); got {y}")
    lo, hi = 0.0, 40.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if math.erfc(mid) > y:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def tj_at_ber(ber: float, ui_ps: int, rj_rms_ui: float,
              sj_pp_ui: float) -> dict[str, float]:
    """Dual-Dirac total-jitter budget at a BER, in UI and ps.

    ``TJ = SJ_pp + 2*Qinv(BER)*RJ_rms``.  Assumes unbounded Gaussian RJ; the
    Irwin-Hall surrogate is bounded at +-6 sigma, so this is a budget.
    """
    if not (0.0 < ber < 0.5):
        raise ValueError(f"ber must be in (0, 0.5); got {ber}")
    qinv = math.sqrt(2.0) * _erfcinv(2.0 * ber)
    tj_ui = sj_pp_ui + 2.0 * qinv * rj_rms_ui
    return {
        "ber": ber,
        "qinv": qinv,
        "tj_ui": tj_ui,
        "tj_ps": tj_ui * ui_ps,
        "eye_height_ui": 1.0 - tj_ui,
    }


def reference_table(
    n_edges: int,
    ui_ps: int,
    *,
    ppm: int = 0,
    sj_peak_ui_q: int = 0,
    sj_freq_hz: int = 0,
    rj_rms_ui_q: int = 0,
    seed: int = 0xACE1,
    dcd_peak_ui_q: int = 0,
    dcd_pattern: Iterable[int] | None = None,
) -> dict[str, Any]:
    """JSON-serializable reference vector plus derived statistics."""
    tie = edge_tie_ps(n_edges, ui_ps, ppm=ppm, sj_peak_ui_q=sj_peak_ui_q,
                      sj_freq_hz=sj_freq_hz, rj_rms_ui_q=rj_rms_ui_q,
                      seed=seed, dcd_peak_ui_q=dcd_peak_ui_q,
                      dcd_pattern=dcd_pattern)
    out: dict[str, Any] = {
        "ui_ps": ui_ps,
        "n_edges": n_edges,
        "ppm": ppm,
        "sj_peak_ui_q": sj_peak_ui_q,
        "sj_freq_hz": sj_freq_hz,
        "rj_rms_ui_q": rj_rms_ui_q,
        "seed": seed,
        "stats": jitter_stats(tie),
        "tie_ps": tie,
    }
    if rj_rms_ui_q or sj_peak_ui_q:
        rj_rms_ui = rj_rms_ui_q / 65536.0
        sj_pp_ui = 2.0 * sj_peak_ui_q / 65536.0
        out["tj_at_ber"] = {
            str(ber): tj_at_ber(ber, ui_ps, rj_rms_ui, sj_pp_ui)
            for ber in (1e-12, 1e-9)
        }
    return out


def write_vector(dest: Path, table: dict[str, Any]) -> None:
    """Write the reference vector ``tb/tb_phase_model.sv`` reads.

    One signed 32-bit value per line, in ``$readmemh`` two's-complement hex, so
    the Verilog side needs no parser at all.  The edge count travels in the TB's
    ``+edges=`` plusarg.
    """
    lines = [f"{(v & 0xFFFF_FFFF):08x}" for v in table["tie_ps"]]
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("\n".join(lines) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="reference jitter/phase vector")
    ap.add_argument("--edges", type=int, default=256)
    ap.add_argument("--ui-ps", type=int, default=10000)
    ap.add_argument("--ppm", type=int, default=0)
    ap.add_argument("--sj-peak-ui-q", type=int, default=0)
    ap.add_argument("--sj-freq-hz", type=int, default=0)
    ap.add_argument("--rj-rms-ui-q", type=int, default=0)
    ap.add_argument("--seed", type=int, default=0xACE1)
    ap.add_argument("--dcd-peak-ui-q", type=int, default=0)
    ap.add_argument("--dcd-alternate", action="store_true",
                    help="stimulus pattern for DCD: alternating 0/1")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    pattern = None
    if args.dcd_alternate:
        pattern = [k & 1 for k in range(args.edges)]
    table = reference_table(args.edges, args.ui_ps, ppm=args.ppm,
                            sj_peak_ui_q=args.sj_peak_ui_q,
                            sj_freq_hz=args.sj_freq_hz,
                            rj_rms_ui_q=args.rj_rms_ui_q, seed=args.seed,
                            dcd_peak_ui_q=args.dcd_peak_ui_q,
                            dcd_pattern=pattern)
    if args.out:
        dest = Path(args.out)
        write_vector(dest, table)
        print(f"wrote {dest}")
    print(json.dumps(table["stats"], indent=1))
    if "tj_at_ber" in table:
        print(json.dumps(table["tj_at_ber"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
