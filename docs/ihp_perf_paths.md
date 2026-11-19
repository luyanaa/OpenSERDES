# IHP130 performance: three measured paths

Measured 2026-11-19 on `ihp-sg13g2` / `sg13g2_stdcell`, 10 ns clock, post-route
STA at `nom_slow_1p08V_125C`.  Every row below is a full LibreLane run that
reached RTL-to-GDS with KLayout DRC and LVS, not an estimate.

## Baseline (what we had)

`spec/bang_bang_4x.yaml`: bang-bang CDR, `OVERSAMPLE=4`, 25 Mb/s, `ppm=700`.

| metric | value |
|---|---:|
| critical path | 8.208 ns |
| WNS at 10 ns | +2.220 ns |
| implied fmax | 128 MHz |
| cells | 2534 |
| core area | 28181 um2 |
| power | 1.721 uW |
| DRC / LVS | 0 / 0 |

The 10 ns constraint is not the limit: WNS is +2.22 ns, so the same netlist
closes at **7.78 ns = 128 MHz**.  Path breakdown from
`56-openroad-stapostpnr/nom_slow/max.rpt`: 0.710 ns launch clock, **7.497 ns of
logic**, 0.710 ns capture clock, 0.100 ns uncertainty, 0.192 ns library setup.
The logic is 26 gates + 6 resizer buffers, ~232 ps per gate on SS.

## PATH 1 — OS=2 with a fixed-oversampling CDR: line rate x2, size /3

`fixed_oversampling_os2`, `allow_os2: true`, `ppm: 0`, 50 Mb/s.

| metric | baseline | PATH 1 | delta |
|---|---:|---:|---:|
| line rate | 25 Mb/s | **50 Mb/s** | 2x |
| critical path | 8.208 ns | **2.142 ns** | **-74%** |
| implied fmax | 128 MHz | 467 MHz | 3.6x |
| cells | 2534 | **892** | **-65%** |
| core area | 28181 um2 | **10435 um2** | **-63%** |
| power | 1.721 uW | **0.579 uW** | **-66%** |
| DRC / LVS | 0 / 0 | 0 / 0 | |

Evidence: `build/pnr/path1_os2.json`.

**What it is not.** This is not a drop-in replacement for the baseline.  A
fixed-oversampling CDR has **no phase detector and no frequency accumulator**,
which is exactly why the path collapses: it only samples at a fixed phase.  The
generator enforces `ppm=0` for it (`gen_ip.py`: "ppm offset requires tracking
CDR"), so it cannot absorb any frequency offset.  The 74% is therefore a
*smaller, cheaper datapath*, not a faster version of the same loop.

**What it is good for.** Source-synchronous links and any link where both ends
share a reference (the overwhelmingly common case for chip-to-chip and
backplane), especially where area and power matter.  It is the cheapest 2x
line-rate win in the whole design.

## PATH 4 — stronger driving cell: no effect, slightly worse

`--set SYNTH_DRIVING_CELL=sg13g2_buf_8/X` against the baseline's `buf_4/X`.

| metric | baseline (buf_4) | PATH 4 (buf_8) | delta |
|---|---:|---:|---:|
| critical path | 8.208 ns | 8.325 ns | +0.118 ns |
| cells | 2534 | 2537 | +3 |
| core area | 28181 um2 | 28261 um2 | +0.3% |
| power | 1.721 uW | 1.724 uW | +0.2% |
| DRC / LVS | 0 / 0 | 0 / 0 | |

Evidence: `build/pnr/path4_drive8.json`.

**Rejected.** The driving-cell strength is not the bottleneck.  Two reasons,
both measured:

1. Most gates on the path have **no stronger version** in this library.
   `o21ai`, `xnor2`, `a221oi` and `a22oi` exist only as `_1`; the path uses 7, 3,
   1 and 1 of them respectively, i.e. 12 of 26 gates cannot be up-sized at all.
2. Relaxing the design rules has no effect either.  Re-running OpenSTA on the
   routed database with `max_fanout 10->32`, `max_slew 0.5->1.5 ns`,
   `max_cap 0.1->0.3 pF` left WNS **bit-identical** (3.527 ns both ways).  The
   6 resizer buffers on the path are 1.438 ns of the 7.497 ns; they are
   necessary, not slack.

So the 232 ps/gate is the SS corner's own gate delay, not a fanout or drive
problem.

## PATH 2 — decouple the multiphase fine-frequency accumulator

`cdr_multiphase` formed `ph_next = ph_q + ph_step` in one adder with all
`FREQ_FRAC` fraction bits in it, and that adder feeds the threshold compare.
`docs/dll_backend.md` section 3 records the consequence: `FREQ_FRAC=10/11` pass
the +700 stress patterns but measure 11.43/10.46 ns pre-PnR and do not close,
with the 16-bit phase add named as the blocker.

`cdr_bang_bang` already had the fix (`inc_fast_r` + registered
`resid_carry_q`).  This applies the same two-stage carry to
`cdr_multiphase`: the low `FREQ_FRAC-FASTF` bits go into a slow accumulator
whose registered carry is the only extra term in the fast adder.

| FREQ_FRAC | full add | fast add now | slow bits |
|---:|---:|---:|---:|
| 8 | 13 | 13 | 0 (no split needed) |
| 9 | 14 | **13** | 1 |
| 10 | 15 | **13** | 2 |
| 11 | 16 | **13** | 3 |

Fast-adder width is now constant in `FREQ_FRAC`.

Functional verification (`tb_cdr_multiphase`, 5000 bits, pattern 3):

| FREQ_FRAC | 0 ppm | +700 ppm | -700 ppm |
|---:|---|---|---|
| 8 | PASS 0 err | PASS 0 err | PASS 0 err |
| 9 | PASS 0 err | - | - |
| 10 | PASS 0 err | **PASS 0 err** | **PASS 0 err** |
| 11 | PASS 0 err | **PASS 0 err** | **PASS 0 err** |

The 10/11-bit widths now pass the stress patterns that previously forced the
default to stay at 8.  Area cost at `FREQ_FRAC=11` vs 8: 975 vs 832 cells
(ABC, `-D 2000`), +17%.

**Not yet measured: the routed A/B at FREQ_FRAC=10/11.** `CDR_MODE=4` is not
emittable from `gen_ip.py` (it needs an external `rx_dll_tap`/`rx_smp`
sampler), so the routed comparison needs a hand-built top.  Until that exists
the shipped default stays `FREQ_FRAC=8`, which is also now functionally clean.

## Summary

| path | what it buys | cost | verdict |
|---|---|---|---|
| 1: OS=2 fixed-oversampling | **2x line rate, -65% cells, -66% power** | loses frequency tracking (`ppm=0` only) | **adopt for source-synchronous links** |
| 2: two-stage carry | 10/11-bit resolution **now passes** stress patterns | +17% cells at 11 bits, one extra cycle latency | **adopted in RTL**; routed A/B pending |
| 4: stronger drive | nothing | +0.3% area, +0.118 ns | **rejected, measured** |

The honest headline: on this 130 nm library the digital core is limited by
~232 ps per logic level at SS, and neither drive strength nor design rules move
that.  The wins come from **removing logic** (path 1) or **keeping the width out
of the critical adder** (path 2) -- not from tuning the physical implementation.
