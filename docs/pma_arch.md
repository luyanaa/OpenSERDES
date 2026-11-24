# High-speed PMA architecture — decoupling line rate from digital clock

Status: direction document, agreed 2026-11-25.  Replaces "raise Fmax of the
4×-oversampling CDR" as the performance route.

## Why the direction changed

Every measured result this cycle points at the same wall: the bang-bang CDR
runs its loop computation in the line-rate clock domain, so Fmax is bounded by
a combinational ring of control logic, not by the wire.

- bang-bang IHP closes at **89 MHz full-ladder**, 101 MHz post-route-only —
  and the 101 MHz closure needs the post-route resizer to split a 31-fanout
  cone plus CTS skew.  That is a *netlist-resizer* artifact, not headroom.
  `docs/bang_bang_pipeline_study.md` exhausted the pipelining options; the
  single-cycle loop is an architectural limit.
- `ihp_perf_paths.md` already concluded the wins come from **removing logic**
  (PATH 1, OS=2, -66% power) or **keeping width out of the critical adder**
  (PATH 2, +0.083 ns for 3 extra freq bits) — not physical tuning.
  Quarter-rate is PATH 1/2 taken to the end: move the loop off the wire rate.
- `cdr_ihp130` just signed off the multiphase-clock + sampler-bank + phase-
  selector hard macro (8-phase, <16 ps routed uniformity, DRC/LVS clean) —
  the `multiphase_clock`/`sampler_bank` blocks in the diagram below already
  exist in silicon-proven form on IHP.

The original SparcLab OpenSerDes reached 2 Gb/s not with an 8 GHz digital
clock but with an external multiphase clock generator + parallel sampler +
register bank + decision logic (DATE 2021 Fig. 7).  This document re-adopts
that structure on top of the current generator.

## Target structure (2 Gb/s NRZ, quarter-rate example)

```
PCS  (word domain, e.g. 62.5 MHz for 32-bit)
  TX FIFO/coding        RX FIFO/coding
       |                    ^
Gearbox / rate conversion
  32:4/16:4 MUX         4:32/4:16 DEMUX
       |                    |
PMA  (high-speed local clock domains)  -------------------------
  4:1 MUX  <--+  PLL/DLL phase gen + PI/CDR loop (slow control)
  500MHz 4ph  |        ^
       |      |        |
  TX driver   |   Sampler bank (data + edge, 8 phases @500MHz)
       |      |        |
       +------+   RX front end (EQ / slicer)
              2 Gb/s channel
```

Only the final MUX, sampler, driver and clock cells touch sub-ns.  At
2 Gb/s the 32-bit PCS runs at 62.5 MHz — comfortably inside what the digital
library already closes.

## Workstreams

### P1 — TX: real gearbox + tree MUX (all-new, recon 2026-11-25)

Recon found **no existing tree to extend**: every serializer emits 1 bit per
serial clock (incl. `serializer_hier_2to1`, a balanced 2:1 *word* tree that
still clocks out 1 bit/clk).  `tx.rate_mode` accepts only `full`|`half`; no
`pma/` dir, no `tx_mux`/`gearbox`/`parallel_mux`.  And the IHP driver lives
in **OpenSERDES**, not adpll_gen — a direct KLayout PCell chain `tx_macro`
(`VSS/VDD/IN/OUT`, 1→3→9 parallel inverters, unit W=5 µm), PEX from Magic in
`build/verify/ihp_tx_macro/` (Ron 45.4/13.8 Ω, tr/tf 33.6/32.7 ps @50 fF,
915.9 µW @1 GHz).  It has no Verilog wrapper, no clock, no data bus.

So the whole front is new RTL: `rtl/tx_gearbox.sv`,
`tx_word_serializer.sv`, `tx_parallel_mux.sv`; PMA `pma/tx_mux4`,
`pma/tx_driver` (wrapping `tx_macro`).  Stage plan (2 Gb/s):

| stage | width | clock |
|---|---|---|
| PCS input | 32 b | 62.5 MHz |
| gearbox 1 | 16 b | 125 MHz |
| gearbox 2 | 8 b | 250 MHz |
| gearbox 3 | 4 b | 500 MHz |
| final | 1 b | 2 Gb/s out |

Early stages plain stdcell regs; last stage a local multiphase clock MUX /
latch MUX / custom macro.  Each stage needs explicit retiming/clock-relation/
hold annotation or ABC re-merges the tree into one long path.  Generator
hook: add a `rate_mode` next to `gen_ip.py:1462` + SDC branch next to `:2068`
+ `DESIGN_FILES` entry (`:2546`).

**Stage A landed 2026-11-25** (`d60eb34`): `rtl/tx_parallel_mux.sv` (real
4:1 latch-MUX tree — two 2:1 latch branches on `clk_ph[0..3]` @500 MHz into a
final 2:1 phase-selected mux, every flop 500 MHz-clocked, `serial_out`
rotates through phases = 4 UI/2 ns word), `rtl/tx_gearbox.sv` (parametric
2N→N ladder), `pma/tx_driver.sv` (PDK-neutral wrapper, `PMA_TX_DRIVER_CELL`
binds `tx_macro` at PnR), `rtl/tx_prbs31_macro.sv` (standalone PRBS31→tree→
driver, parallel LFSR keeps digital off line rate).  Verified: 20000 bits
0 errors, per-phase 0, yosys clean (no comb merge).  Next: real-clock Stage B
(multiphase_clock macro) + PnR on the 500 MHz domain.

### P2 — RX: parallel multiphase sampler bank (mostly a rewrite)

Recon 2026-11-25: **nothing to extend** — every existing loop is a single-
sample Alexander filter, and `cdr_ihp130`'s 8 taps divide *one sample-clock
period* (1.25 ns/8 at 100 MHz) to place one edge, not 4-data+4-edge across a
quarter-rate cycle.  Recon detail (`cdr_bang_bang.sv`/`cdr_multiphase.sv`):

- `enable` is run/freeze, not "update this UI": low holds phase and only
  clears `strobe_q`/`kick_q`.  While enabled the phase word advances every
  `clk`, and the vote moves only on a centre-hit+transition (≤1 vote/UI).
- bang-bang `KP = 1<<(NCO_FRAC-KP_SHIFT)` (2048 LSB = 312 ps @100 MHz),
  `KI=1`, FMAX `1<<(FR-2)`; multiphase `KP` = 0.25 tap = 312 ps, `KI=1`,
  freq applied once/UI at the last slot.
- CDR_MODE=4 handshake to generalize: `rx_dll_tap[PH_W-1:0]` is a register
  slice `ph_q[FREQ_FRAC +: PH_W]` (constant across a UI), `rx_smp` is the bit
  taken at that tap, valid **next clk**.  A quarter-rate bank needs 8 phase-
  clocked samples in, a summed/majority early-late, and an enable that fires
  once per quarter-rate cycle.
- Reuse **as-is**: integral/proportional math + sign (`early=+1` delays),
  KI=1 incrementer, FMAX clamp, leak/lock/stale counters, `freq_trim` probe,
  `receiver_sampler` as a leaf, `PLL_MULTIPHASE_DCO`'s 8-phase port (re-timed
  to UI/2 spacing across one quarter-rate period).
- Rewrite: the scalar vote {-1,0,+1}, the per-clock slot walk, centre/edge
  thresholds (they assume one sample/clk, one edge+centre/UI), scalar
  `tap`/`smp` + park mux, `enable`, and `receiver_pair_sampler`.
- **Sign trap**: adpll `src/alexander_pd.v` is 2× only and uses the
  **opposite** early/late sign vs OpenSERDES — do not reuse it without
  flipping.

Quarter-rate Alexander: 8 phases @500 MHz → 4 data + 4 edge samples/cycle,
4-way vote merge into a decimated loop. 16 UI/update → 125 MHz, 32 UI →
62.5 MHz.  Re-derive KP/KI/latency — decimation changes bandwidth/phase
margin/ppm tracking.  Elastic-correction into the gearbox must handle PI
phase wrap + independent-ref ppm + clean boundary shifts.

New files (rewrite, not extend): `cdr_parallel_bbpd.sv`,
`cdr_decimated_loop.sv`, `cdr_phase_controller.sv`, `rx_parallel_align.sv`,
`rx_parallel_gearbox.sv`; PMA `sampler_bank`, `phase_interpolator`,
`multiphase_clock`.  Tune against `tb_cdr_multiphase` pattern 3 (the
low-transition-density case the 10/11-bit loop passed).

**Stage A landed 2026-11-25** (`1ec65e6`): `sampler_bank.sv` (8
`receiver_sampler` leaves, data=even / edge=odd `clk_ph`),
`cdr_parallel_bbpd.sv` (per-UI Alexander vote across the 4-UI group,
summed −4..+4, `early=+1` delays = OpenSERDES sign),
`cdr_decimated_loop.sv` (bang-bang KP/KI/FMAX/leak/lock/stale re-derived
for decimation, updates once/DECIM(16) UI = **125 MHz**, slip/elastic
reporting), `rx_parallel_gearbox.sv`+`rx_parallel_align.sv` (group→word
assembly + count-based slip repair + skid/rotate align),
`cdr_parallel_top.sv` (wires it, `pi_code`→phase map + Stage-B PI plug-in
documented).  Verified: PPM=0 full-density + pattern-3 both lock, 4000
bits 0 errors, no `FREQ_FRAC≥10` workaround needed.  Open (in-spec):
nonzero-ppm elastic + `PH_OFF` capture range → Stage-B `CLK_FOLLOW`
clocking (RTL already supports both modes, TB not yet closed).

### P3 — sampler catalog (re-admit non-stdcell samplers for PMA)
Previous cycle swapped all DFFs to PDK-native DFF — correct for generic
digital, but high-speed PMA needs exceptions.  Build a *generatable* catalog:

| sampler | trait | use |
|---|---|---|
| stdcell DFF | easiest port/verify | low-speed PHY |
| NAND master-slave | ~original OpenSerDes | legacy reproduction |
| TG latch | multiphase retiming | half/quarter-rate |
| StrongARM / double-tail | low input swing | high-speed RX |
| CML / SiGe latch | high f, low swing | IHP high-perf PHY |

Each macro is independently characterized (setup/hold/Clk2Q/aperture/
metastability/offset/noise/power) and emits real Liberty/RNM/PEX views.
`receiver_sampler.sv` stays the generic functional model, not the speed
evidence.

## Analog-macro leverage

IHP TX driver PEX: tr/tf 33.6/32.7 ps, Ron 45.4/13.8 Ω, 915.9 µW @1 GHz —
the driver is NOT the line-rate limit.  Caveats before claiming PHY speed:
- 50 fF load vs paper's ~2 pF; need 100 fF/500 fF/1 pF/2 pF sweep + pad/ESD/
  package/channel.
- PU/PD asymmetry → ~5.5 pt duty-cycle distortion @1 GHz (~0.11 UI jitter);
  fix N/P ratio + stage-ratio sweep (not fixed 1:3:9).
- Measure DDJ/DCD/eye/Ebit under PRBS31, not square-wave.

Sky130: restore original 2 GHz TX + resistive-feedback RX as the same-process
reproduction baseline (short/low-loss link first, then low-swing/high-loss).

## ADPLL integration — staged, not blocking

| stage | clock backend | purpose |
|---|---|---|
| A | ideal external multiphase | validate MUX/sampler/parallel CDR — **runs now** |
| B | extracted DLL/DCO/PLL macro | real jitter/skew/PVT — cdr_ihp130 already provides this form on IHP |
| C | adpll_gen auto-gen + integrate | full cross-PDK PHY |

2 Gb/s quarter-rate needs a stable **500 MHz multiphase** macro and a
62.5–125 MHz digital loop — not 2–8 GHz.  ADPLL PEX'd freq range/PN/full
multiphase signoff is still open, so it must not gate stage A.

## cdr_ihp130 as the 500 MHz multiphase clock — a real redesign, not a param tweak

`cdr_ihp130` (signed off 2026-11-24) is the proven multiphase-clock +
sampler-bank + phase-selector form, but its ring cannot run at 500 MHz as
configured: the 513-stage fixed delay line self-oscillates at ~23 MHz at SS
(513 × ~42 ps × 2 ≈ 43 ns).  It must become a **short tunable ring**, not a
long fixed line — this is the `multiphase_clock` block of the PMA and is
in-house work.

Constraints found while measuring:
- `NSTAGE = 2^DELAY_BITS + 1` is forced odd (ring needs an odd inversion
  count) but also must divide evenly by 8 for integer tap spacing —
  `2^N + 1` is never divisible by 8, so `tap_stage(t) = floor(t·NSTAGE/8)`
  is a floor approximation.  At NSTAGE=513 the quantization error is small;
  at a *short* ring it is not (17 stages → 2.125/tap).
- Target 500 MHz: period 2 ns → `NSTAGE × stage_delay ≈ 1 ns`.  With
  `sg13g2_inv_1` ≈ 42 ps at SS, `STEP≈3` (`NSTAGE≈25`) gives ~476 MHz,
  `STEP≈4` (`NSTAGE≈33`) ~372 MHz.  So the multiphase line should be
  `NSTAGE = 8·STEP + 1` (odd, exact 8-way taps), STEP≈3–4, *with tuning*
  (the CDR PI sweeps phase across taps; a small delay trim or the mux's
  parked-tap walking provides rate adjust), not `2^DELAY_BITS`.

Direction (owner: in-house): re-spec `pll_multiphase_dco` for `NSTAGE =
8·STEP + 1`, add a per-stage delay trim (load cap or a 2nd parallel inv) so
the PI can pull frequency/phase, re-run the `MANUAL_GLOBAL_PLACEMENTS`
snake at the new length, and re-measure tap uniformity to <30 ps at the
target rate.  The sampler/mux/park logic in `cdr_top.v` carries over
unchanged — only the DCO line shortens.

## Per-PDK roadmap (milestones, not closed rates)

| PDK | first | second | high-speed backend |
|---|---|---|---|
| GF180MCU | 250–500 Mb/s | 1 Gb/s | 8-phase CMOS/latch |
| Sky130 | 500 Mb/s–1 Gb/s | 2 Gb/s | original RX + quarter-rate CMOS |
| IHP SG13G2 | 1–2 Gb/s | 2.5–5 Gb/s | CMOS / SiGe CML |
| ICS55 | 1–2 Gb/s | 2.5–5 Gb/s | CMOS multiphase / HS latch |

Priority: IHP (new-structure perf) + Sky130 (same-process reproduction of the
2 Gb/s paper); GF180/ICS55 share digital RTL + generator + contracts and swap
physical macros.

## Prerequisites / debt

- **CI lint regression at HEAD** (Verilator ~35 warnings → synthesis skipped):
  new watchdog/protocol/multilane ports (`comma_lock_timeout`,
  `comma_lock_stuck`, `i_rd_init`, `i_rd_load`) unconnected in older wrappers;
  fix first — new link-health must not break baseline verification.  (Lane
  dispatched 2026-11-25.)
- bang-bang-top **LVS residual** (`tech.yaml:52`, `physical_signoff_mhz:
  null`): the 89 MHz timing_closed point is STA-clean but the top-level
  KLayout LVS still has 1 residual (same open item as ez130) — do not treat
  the earlier DRC/LVS-clean experiment as a full physical signoff on HEAD.
- Raw PCS (`serdes_raw_pcs_top.sv`) exposes `tx_usr_clk`/`rx_rec_clk` but the
  internals still run on the unified `clk` — the word clock is only a divided
  output.  For the PMA split to mean anything, PCS coding/CRC/PRBS/FIFO/word
  alignment must actually move into the word-clock domain (P2 dependency).
- Existing `serializer_half_rate`/`receiver_pair_sampler` are half-rate
  *parallel banks*, not real DDR/quarter-rate PMA — they still emit/sample
  one bit per `clk_bit`.
```

## Acceptance anchors

- TX macro: 2 Gb/s PRBS31 out, post-route STA closed on the 500 MHz domain,
  measured eye/DDJ/DCD on IHP.
- RX loop: quarter-rate Alexander vote-merge re-derivation documented,
  transition-density pattern 3 (the TB the 10/11-bit multiphase passed)
  re-run on the decimated loop.
- No regression: CDR_MODE=4 / `ddr` / `source_sync`/`independent`
  clock_source fail-closed semantics unchanged; 50 Mb/s path-1 boundary
  preserved.
