# Reference jitter / phase model

`verification/channel/phase_model.py` (Python) and `tb/jitter_clock.sv` (RTL)
are **one model with two implementations**.  They use identical integer
arithmetic, so the RTL produces the same TIE sequence as the Python reference
bit for bit; `tb/tb_phase_model.sv` loads a vector file the Python side writes
and fails on the first mismatch.  That equivalence is enforced in
`make phase-model-rtl` and in `scripts/regress.py` (`phase_model_{rj,sj,ppm,dcd}`).

This note documents what the model is, what it deliberately is not, and where
the pieces live.

## Interface

| side | entry point |
|---|---|
| Python | `verification.channel.phase_model.edge_tie_ps(n_edges, ui_ps, ...)` |
| Python CLI | `python3 -m verification.channel.phase_model --edges 64 --ui-ps 10000 --out vec.hex` |
| RTL | `tb/jitter_clock.sv` (`edge_valid`, `edge_index`, `tie_ps`) |
| equivalence TB | `tb/tb_phase_model.sv` (`+vector=<hex file>`) |

The vector file is one 32-bit hex word per modelled edge (two's complement), so
the RTL needs no text parser.

## What it models

For edge `k` (one rising sampling edge, one UI apart):

```
tie_k = ppm_term + sj_term + rj_term + dcd_term            (ps)

ppm_term = trunc(-PPM * k * UI_PS / 1e6)                    frequency offset
sj_term  = (SJ_PEAK_UI_Q * UI_PS * sin_lut(phase_k)) >> (SJ_FRAC_BITS + 15)
rj_term  = (RJ_RMS_UI_Q  * UI_PS * gauss_k)          >> (RJ_FRAC_BITS + 16)
dcd_term = ((DCD_PEAK_UI_Q * UI_PS) >> DCD_FRAC_BITS) * sign(transition)
```

with

```
gauss_k   = (sum of 12 xorshift draws in units 1/65536) - 6   mean 0, var 1
sin_lut   = 64-entry Q1.15 quarter-wave sine (sin_lut[32] = 32768 = 1.0)
phase_k   = k * inc,  inc = trunc(2^32 * SJ_FREQ_HZ * UI_PS / 1e12)
xorshift  = x ^= x<<7; x ^= x>>9; x ^= x<<8      (16 bit, zero seed -> 0xACE1)
```

| axis | parameter | meaning |
|---|---|---|
| RJ | `RJ_RMS_UI_Q` / `rj_rms_ui_q` | white TIE RMS per edge, `2^frac` scaled UI |
| SJ | `SJ_PEAK_UI_Q`, `SJ_FREQ_HZ` | sinusoidal **peak** amplitude (p-p is 2x), absolute frequency |
| ppm | `PPM` | frequency offset of the sampler vs. the data |
| DCD | `DCD_PEAK_UI_Q` | duty-cycle distortion, signed by the data transition |

`ppm = +100` means the sampling clock is fast, so edges walk forward through the
eye and the term is `-ppm*k*UI/1e6` (negative TIE = earlier).

## Why integer arithmetic and an Irwin-Hall surrogate

A Gaussian from an inverse CDF cannot be reproduced exactly in both languages
without sharing a transcendental implementation.  The Irwin-Hall surrogate can:

* 12 uniform draws `x/65536` from a 16-bit xorshift, summed, minus 6;
* mean 0 and variance **exactly** 1, so no scaling constant has to agree;
* no `math.sin` / `sin()`: both sides index the same 64-entry table.

The ppm increment is factored as `2^32/1e12 = 2^20/244140625` and evaluated in
quotient/remainder form, because `2^32 * f * UI` overflows a 64-bit integer for
realistic values (5 MHz at 10 ns = 2.1e20).  The naive form silently truncated
to zero in iverilog; that was a real bug found by the equivalence test.

## Verified equivalence

`make phase-model-rtl` and the four `phase_model_*` regress cases compare the
RTL against the Python vector for:

| case | parameters | result |
|---|---|---|
| `rj` | 0.02 UI RMS white, seed 44257 | bit-exact (`min=-352 max=441` ps) |
| `sj` | 0.25 UI peak at 5 MHz | bit-exact (`min=-2393 max=2392` ps) |
| `ppm` | +150 ppm | bit-exact (`min=-94 max=0` ps) |
| `dcd` | 0.1 UI, alternating pattern | bit-exact (`min=-1000 max=1000` ps) |
| mixed | RJ + SJ + ppm together | bit-exact (96 edges) |

## Statistics

`jitter_stats` estimates the white part from the first difference
(`sqrt(var(diff)/2)`), which cancels the locally-linear deterministic terms
(ppm ramp, low-frequency sinusoid) while leaving the white term.  `tj_at_ber`
is the dual-Dirac budget `TJ = SJ_pp + 2*Qinv(BER)*RJ_rms`; `Qinv` comes from a
dependency-free bisection on `math.erfc`.

## Limits (do not overclaim)

- **Reference, not a channel.**  No ISI, no reflection, no CTLE/DFE, no
  crosstalk, no SSC modulation profile.  `rb/jitter.py` remains the model for
  TIE analysis from measured data; this module is the *reference generator* the
  digital loops and the RTL TBs share.
- **Bounded "Gaussian".**  The Irwin-Hall surrogate is bounded at ±6 sigma.  At
  BER 1e-12 the missing tail matters, so `tj_at_ber` is a budget, never a
  measured BER.
- **Quantised sine.**  A 64-entry Q1.15 table loses ~4% near the peak
  (0.25 UI requested -> 0.239 UI observed).  That is the table, not an error;
  raise the table if a test needs more.
- **No phase-noise process.**  White RJ here is independent per edge: no 1/f, no
  edge-to-edge correlation, no oscillator spectrum.  Use `verification/channel/
  jitter.py` plus measured data when the spectrum matters.
- The model produces TIE, not a waveform: it does not move the sampler and it
  does not know about the CDR loop.  Feeding `tie_ps` into a loop model is the
  caller's job (that is what the RTL output is for).
