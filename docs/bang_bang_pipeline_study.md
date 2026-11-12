# Bang-bang CDR pipeline-cut study

> Scope: **pre-PnR structural analysis**, not a design change.  All numbers
> below are from `yosys 0.52` + `abc -liberty sky130_fd_sc_hd__ss_100C_1v60`
> + OpenROAD `sta` at a 10 ns clock with ideal clocks and no SPEF.  They
> measure **logic depth**, not routed timing.  The corresponding routed
> sky130 number is 24.23 ns in the ss corner
> (`build/pnr/cross_pdk_close/sky130/runs/close_25ns_sky130`); pre-PnR is
> ~1.3x slower because it has no buffering/sizing.  Use ratios, not absolute
> nanoseconds, when comparing variants.

## 1. Root cause: a single-cycle closed loop

The post-route critical path starts at `g_bang_bang.u_cdr.freq_q[11]` and
returns to a `freq_q` D input within **one clock**.  Decomposed from the
45-gate pre-PnR path (31.485 ns):

```
freq_q --> inc_v = CLKINC + freq_q - kick_q   (20b add/sub + saturation mux)
       --> phase_next = phase_q + inc_v       (21b ripple-carry: 7x maj3)
       --> wrap = phase_next >= ONE           (21b compare)
       --> phase_w = wrap ? phase_next-ONE : phase_next  (21b sub + mux)
       --> cx_c/cx_e vs center_e/edge_e      (4x 21b compare)
       --> center_hit / edge_hit --> trans --> vote (Alexander BBPD)
       --> freq_next = freq_q - KI*vote + clamp (20b add + 2 compares)
       --> freq_q (D)
```

Three ripple-carry segments (`maj3_1` chains, ~13 cells) account for about
half the delay.  The rest is comparators, saturation muxes and the PD.

**Why it is one cycle:** the BBPD must vote on the phase event of the *current*
UI and have that vote change the NCO increment for the *next* clock.  The
`kick_q` proportional term is explicitly a one-clock increment offset, and
`c_done_q`/`e_done_q` enforce exactly one centre and one edge event per UI
across the wrap boundary.  `docs/cdr_receiver_digital.md` §7.1 documents
this, and the `nxt_c`/`nxt_e` wrap logic and the per-UI leak semantics were
both fixed for off-by-one bugs.  These states are tightly coupled to the
phase accumulator; they are not independent datapath stages.

## 2. Measured variants (pre-PnR, sky130 ss, 10 ns clock)

| Variant | arrival | cells | DFF | `tb_cdr_bang_bang` 0 / +700 / -700 ppm |
|---|---:|---:|---:|---|
| baseline, NCO_FRAC=16 | 31.49 ns | 699 | 78 | PASS / PASS / PASS |
| NCO_FRAC=18 | 31.26 ns | 769 | – | – |
| NCO_FRAC=14 | 26.33 ns | 667 | – | – |
| NCO_FRAC=12 | 23.25 ns | 622 | – | – |
| yosys `wreduce` + `opt -full` | 31.49 ns | 699 | 78 | (no change) |
| **P3**: phase width PW 21 -> 19 | **26.40 ns** | **588** | 78 | **PASS / PASS / PASS** |
| P1: two-stage pipeline | 19.05 ns | 705 | 116 | **FAIL / FAIL / FAIL** |
| P2: three-stage pipeline | 16.68 ns | 777 | 162 | **FAIL / FAIL / FAIL** |
| P1 + NCO_FRAC=12 | 13.67 ns | 621 | 104 | FAIL |
| P2 + NCO_FRAC=12 | 11.32 ns | 694 | 142 | FAIL |
| **A**: exact widths + folded constants (FR=16) | **20.64 ns** | **573** | 73 | **PASS (all 9 ppm, all 4 patterns)** |
| **B**: A + re-timed 2-stage PD/filter pipeline | **18.71 ns** | 575 | 77 | **FAIL (0 ppm 1170/5000)** |
| A with FR=12 | 16.94 ns | 483 | – | (see D below) |
| A with FR=10 | 15.72 ns | 442 | – | (see D below) |
| A with FR=8 | 16.15 ns | 394 | – | (see D below) |
| A with FR=6 | 11.19 ns | 352 | – | (see D below) |

Functional results (5000 bits, PRBS):

```
baseline  PPM=0    PASS  freq_trim=0
P1        PPM=0    FAIL  errors=2550/5000  freq_trim=12
P2        PPM=0    FAIL  errors=4671/5000  freq_trim=-37
P2        PPM=700  FAIL  errors=4675/5000  freq_trim=-1276
```

- `P1` registers the phase accumulator outputs and feeds the registered
  `phase_q_r`/`phase_next_r`/`phase_w_r`/`wrap_r`/`s_r`/`*_done_r` into
  event detection + PD; `phase_q <= phase_w_r`.
- `P2` additionally registers `inc_v` and the pre-accumulation state.
- `P3` only narrows `PW` from `FR+PH_W+3` to `FR+PH_W+1` (19 bits at
  FR=16).  This is sufficient because `ONE = 2^(FR+PH_W)` and
  `inc_max < 2^(FR+1)`, so `phase_next < 2^(FR+PH_W+1)`.  **Margin is
  tight** — `center_e` can reach `2^(FR+PH_W) + 3*2^FR` at
  `PHASE_CTRL_W=2`; a larger `phase_offset` width or OVERSAMPLE needs a
  re-derivation before this is safe to ship.

## 3. Findings

1. **Width reduction alone cannot reach 10 ns.**  16 -> 12 buys only 26%
   (31.5 -> 23.3 ns), and FR=12 is already rejected by
   `docs/cdr_receiver_digital.md` §7.4 (244 ppm/LSB, 0.2 UI drift per 10^4
   UIs).  `wreduce` finds nothing extra; the synthesis already does it.

2. **Mechanically inserting pipeline registers breaks the CDR.**  P1/P2 cut
   the path by 40-47% but mis-time the event state machine: event detection
   consumes one cycle of phase state while `phase_q`/`kick_q`/`c_done_q`/
   `e_done_q`/`wrap` and the leak counters advance on another.  The result is
   ~50% bit errors at 0 ppm and a runaway `freq_trim` on P2.  This does
   **not** prove a correctly re-timed pipeline is impossible, but it proves
   that a local register insertion is not a valid cut.

3. **Pipeline stages do not balance.**  P1's worst stage is the comparator / PD
   logic (no `maj3` on the path); P2's worst stage moves back to the phase
   accumulator (14 `maj3` cells).  Cutting `inc_v` out (P2 vs P1) buys only
   12% because the ripple-carry chain simply relocates.

4. **Even the best combination misses the target on validated parameters.**
   P2+FR=12 reaches 11.32 ns pre-PnR (~8.7 ns routed by the 0.77 ratio), but
   it costs both the rejected NCO resolution **and** a 2-cycle loop delay.
   With FR=16 and a 1-cycle loop, no variant gets below 19 ns pre-PnR.

5. **Loop delay is itself a risk.**  A bang-bang loop with two registered
   stages adds 1-2 clocks of pure delay between PD vote and NCO response.
   That reduces phase margin of the second-order loop and can produce limit
   cycling or slower pull-in at ±1000 ppm / 6% transition density.  Any
   re-timed design needs closed-loop behaviour checks (settling, overshoot,
   frequency-step response), not just bit-error counts.

6. **Only the single-cycle-preserving change worked.**  P3 (PW compression)
   is function-equivalent on 0/±700 ppm, saves 16% delay and 16% cells, and
   touches no loop timing.  It is the low-risk direction, but -16% is not
   enough for 100 MHz.

7. **A (exact widths + folded constants) works and is the largest safe win.**
   See §4.1.

## 4. Four directions, each verified

### 4.1 Direction A - exact widths, folded constants, single-cycle loop kept

Arithmetic-only change, no loop-timing change.  Transformations:

- `PWQ = FR+PH_W` (18 bits) for `phase_q`, `PWN = PWQ+1` (19) for the sum,
  `FW = FR+2` (16) for the loop state (the old margins were `+3` and `+4`).
- `wrap = phase_next[PWQ]` - a single bit, no 19-bit comparator.
- `phase_w = phase_next[PWQ-1:0]` - truncation, no subtract and no mux.
- `center_e = phase_offset * CLKINC` - a shift; `CENTER+CENTER == ONE` and
  `(x + ONE) & (ONE-1) == x` collapse the old add/multiply/mask.
- `edge_e = CENTER` is a single bit, so that compare is one bit wide.
- Clamp moved onto `delta = freq_q - kick_q` (16 bits).  The old lower clamp
  `inc_v < CLKINC/4` was **unreachable** (`freq_q >= FMIN`, `|kick_q| <= KP`),
  so the 20-bit lower comparator is deleted; the upper clamp becomes a 16-bit
  compare.
- `inc_v = CLKINC + delta` (17 bits), `freq_next` is an incrementer because
  `KI == 1` and `vote in {-1,0,+1}`.

Result: **20.64 ns (-34%), 573 cells (-18%), 73 DFF (-5%)**, and the full
functional suite passes: 0 / +-100 / +-300 / +-700 / +-1000 ppm, patterns
0-3 (PRBS, 256-bit run, bursty, 6% density), `tb_cdr_phase_offset`
(offset 0-1), `tb_cdr_leak`.  `freq_trim` matches the baseline exactly
(-43 at +700 ppm, +35 at -700 ppm).

**Landed** in `rtl/cdr_bang_bang.sv`; final pre-PnR measurement of the landed
file is **20.61 ns, 550 cells, 73 DFF** (105/105 regression cases pass, lint
clean).

#### Routed results (sky130, LibreLane 3.0.17)

| target | variant | CP (ss) | WNS (ss) | cells | power | DRC/LVS |
|---|---:|---:|---:|---:|---:|---|
| 20 ns | baseline | 22.29 ns | -1.69 | 2214 | 1.53 uW | clean |
| 20 ns | **A** | 22.41 ns | -1.72 | **2050** | **1.23 uW** | clean |
| 10 ns | baseline | 18.87 ns | -8.27 | 2186 | – | clean |
| 10 ns | **A** | **16.48 ns** | **-5.76** | **2037** | – | clean |
| 22 ns | **A** | 22.41 ns | **0 (MET, +0.28)** | 2050 | 1.12 uW | clean |
| 25 ns | baseline | 24.23 ns | 0 (MET) | 2212 | 1.19 uW | clean |

`critical_path_arrival_ns` includes clock insertion delay, so it can exceed
the period while still meeting timing (the 22 ns row has arrival 22.41 and
slack +0.28).  Use WNS, not the arrival, for closure claims.

**Verified closure: A closes at 22 ns (WNS 0, hold 0, DRC 0, LVS 0), where
the baseline needed 25 ns.**  That is 40 -> 45.5 MHz sample clock (+14%),
still far from the 100 MHz target.

The honest reading: the pre-PnR -34% does **not** map to -34% routed.
At a tight 10 ns target the routed path improves **-12.7%** (18.87 -> 16.48)
and WNS improves by 2.5 ns, because the resizer works harder and the shorter
logic gives it fewer levels to size.  At a relaxed 20 ns target the tool stops
optimising and both variants land at ~22.4 ns, so timing is flat - but A still
saves **7% cells, 5% area and 20% power** (smaller loop arithmetic = less
switching).

Consequence: A is a real PPA win (area, power, and ~13% routed depth) but it
does **not** by itself close 100 MHz; the critical path is still
`u_cdr.freq_q` -> NCO -> PD -> `freq_q`.

One empirical detail worth keeping: the non-wrap branch originally compared
`phase_w` instead of `phase_next` (they are equal when `!wrap`).  That maps
**4 ns slower** (24.62 vs 20.61 ns, 552 vs 550 cells) because abc reuses the
adder carry chain for the `phase_next` compare but not for the truncated
`phase_w` slice.  Functionally identical, but the comparator operand matters.

### 4.2 Direction B - re-timed two-stage PD/filter pipeline: rejected

Phase accumulation stays single-cycle (it must, to keep one UI per clock);
only the PD result and loop filter move behind a register.  The done flags
(`c_done_q`/`e_done_q`) and `wrap` stay with the phase stage.

Result: **18.71 ns, 575 cells, 77 DFF**, but **functional FAIL** at 0 ppm
(1170/5000 bit errors, `freq_trim` stays 0, first error at bit 3).  Three
independent implementations (P1, P2, B) all fail, so this is structural, not
a one-off bug: `d_q`/`d_prev_q`/`d_out_q`/`strobe_q` and the event flags
must advance on the *same* cycle as the phase event, and delaying the PD
result by one clock shifts the strobe relative to the recovered bit.

Even if a correct re-timing were found, it buys **9% over A** (18.71 vs
20.64) at the cost of a two-cycle loop delay, ~2x the re-verification
surface, and an open loop-stability question.  Not worth it.

### 4.3 Direction C - accept the verified 25 ns point: available today

The routed sky130 result already exists and is clean: 25 ns / 10 Mb/s,
WNS 0 in all three corners, hold 0, Magic DRC 0, KLayout DRC 0, LVS 0,
antenna 0, 1.19 uW (`build/pnr/cross_pdk_close25_sky130.json`).  This is a
verified `POST_ROUTE_TIMING` + `PHYSICAL_SIGNOFF`-clean point at 40 MHz
sample clock.  It needs no RTL change.

### 4.4 Direction D - change the architecture: insufficient on its own

Removing fractional NCO bits is the cheapest architectural change.  Measured
on the A structure (pre-PnR, sky130 ss, 10 ns):

| NCO_FRAC | arrival | cells | 1 LSB (ppm) | usable? |
|---:|---:|---:|---:|---|
| 16 | 20.64 ns | 573 | 15 | yes |
| 12 | 16.94 ns | 483 | 244 | marginal (rejected by §7.4 for drift) |
| 10 | 15.72 ns | 442 | 977 | no (+-488 ppm static error) |
| 8 | 16.15 ns | 394 | 3906 | no (+-1953 ppm) |
| 6 | 11.19 ns | 352 | 15625 | **no** (+-7813 ppm >> the +-1000 ppm to track) |

Only FR<=6 reaches 10 ns territory, and its +-7813 ppm quantisation error
exceeds the +-1000 ppm offset the loop must track, so it cannot lock.  Even
FR=10 (+-488 ppm) is not a usable operating point.  A real
architectural change (integer counter + multiphase select, or a different
PD/NCO partition) is a new design, not a parameter change, and 4x sampling
already has less jitter margin (docs §6 records the 2x/baud-rate negative
result).

### 4.5 Direction C2 - register only the NCO control word (sampling untouched)

The P1/P2/B failures came from delaying the **sampling** pipeline.  C2 keeps
every sampling-side signal on its original cycle and moves only the
frequency-control update behind a register:

```
control path (1 clock behind):
    inc_c = CLKINC + clamp(freq_q - kick_q)
    inc_r <= inc_c
fast path (every clock, unchanged):
    phase_next = phase_q + inc_r
    wrap / phase_w / centre-edge events / d_q / d_out_q / strobe_q / done flags
```

`phase_q`, `center_hit`, `edge_hit`, `d_q`, `d_prev_q`, `e_q`,
`d_out_q`, `strobe_q`, `c_done_q`, `e_done_q`, leak and lock all keep
their original timing.  The only cost is one extra clock of loop latency
between the frequency estimate and the NCO control word.

| variant | pre-PnR | routed 10 ns CP | routed WNS | cells (10 ns run) |
|---|---:|---:|---:|---:|
| baseline | 31.49 ns | 18.87 ns | -8.27 | 2186 |
| A (exact widths) | 20.61 ns | 16.48 ns | -5.76 | 2037 |
| **C2 (control-word delay)** | **13.85 ns** | **12.04 ns** | **-1.32** | 2109 |

C2 is **-27% routed depth vs A** and **-36% vs baseline** at the same 10 ns
target, and gets within 1.3 ns of 100 MHz.

Target-vs-achieved caveat (same effect as A): the resizer's effort scales with
the constraint, so a *relaxed* target does not produce a better result.  C2
across three targets (same RTL, same flow):

| target | CP | WNS |
|---|---:|---:|
| 10 ns (100 MHz) | 12.04 ns | -1.32 |
| 12 ns | 13.57 ns | **-0.91** |
| 15 ns | 17.32 ns | -1.63 |

So C2 does **not** have a verified closure point in this range; its best
result is -0.91 ns at the 12 ns target and -1.32 ns at 100 MHz.  The tool's
achieved CP always exceeds the target by 1.3-2.3 ns and does not improve as
the target is relaxed.  Compare variants at the **same** target only; do not
read a relaxed run as the closure point.

Full functional suite passes:
0/+-100/+-300/+-700/+-1000 ppm, patterns 0-3 (incl. 6% density),
`tb_cdr_phase_offset`, `tb_cdr_leak`, 105/105 regression, lint clean.
DRC/LVS/antenna clean in the routed run.

Why it works where P1/P2 failed: the event state machine (`c_done_q`/
`e_done_q`, wrap, `nxt_c`/`nxt_e`) is fed by `phase_q` and `phase_next`
in the same cycle as before; only `inc_r` is registered, so the sampling
instant is unchanged.  The loop still spans two cycles, but through a single
clean register on the control word.

### 4.6 C2 loop-stability characterisation

Same RTL, 100 MHz clock, PRBS unless noted (tb_cdr_bang_bang with the new
steady-state / step diagnostics).

| check | result |
|---|---|
| capture range (5000 bits, PRBS) | PASS from **-3000 to +4000 ppm**; fails at -4000 (38 errors) and +5000 |
| frequency step 0 -> +700 ppm | **0 bit errors** (relock 0 bits) |
| frequency step +700 -> -700 ppm | **0 bit errors** |
| frequency step -1000 -> +1000 ppm | **0 bit errors** |
| 100k bits at 0 ppm | PASS, freq_q span **3 LSB** (limit cycle), mean 0 |
| 100k bits at -1000 ppm | PASS, freq_q span **29 LSB**, mean 65 |
| 100k bits at +1000 ppm | PASS |

Interpretation: the one-cycle control latency does **not** destabilise the
loop over the +-1000 ppm spec (2-4x margin), it does not lose bits across a
+1000 ppm step, and the residual limit cycle is small (3 LSB = 46 ppm p-p at
0 ppm).  The larger span at 500-1000 ppm (28-31 LSB) is the bang-bang loop
dithering around the offset; it is a bounded limit cycle, not a run-away.

Capture is asymmetric (-3000 / +4000).  That is expected for a bang-bang
loop with a leak.  The -4000 test may also be limited by pull-in time inside
the 5000-bit window rather than a hard capture edge; it was not re-run with a
longer window.

### 4.7 A further threshold cut was tried and reverted

Because `center_e = phase_offset << FR` and `edge_e = 2^(PWQ-1)` are aligned
to FR, the centre/edge tests only need the accumulator high bits (2-3 bits at
OS=4) instead of 18-19 bit comparators.  That measured **13.31 ns pre-PnR vs
13.85 for C2**, but **routed 13.02 ns / WNS -2.34 at the 10 ns target vs
12.04 / -1.32 for C2** -- worse where it counts.  The smaller comparators
mapped into worse cells/placement.  Reverted; C2 remains the branch state.
This is a caution against judging a change by pre-PnR depth alone.

**Still open for C2:** the last ~1.3 ns to 100 MHz.  Two further cuts were
evaluated and neither is worth shipping:

- threshold high-bit compares: better pre-PnR, worse routed (§4.7);
- a **second control stage** (register `trans`/`vote` so the loop filter runs
  one more cycle later): 13.05 ns pre-PnR vs 13.85 for C2, i.e. only 0.8 ns,
  at the cost of a second cycle of loop latency and a full re-characterisation.

So C2's honest position is **12-13 ns (77-83 MHz)** for this architecture on
sky130.  Reaching 100 MHz needs the multiphase/DLL/phase-interpolator backend
or a different PD/NCO partition, not more local arithmetic.  See §5.

## 5. Recommendation

1. **A and C2 are both on master.**  A (`63c9f6d`) is the reference
   implementation: -34% pre-PnR depth, -7% cells, -20% power at an equal
   target, -12.7% routed depth at the 10 ns target, and the verified routed
   closure at **22 ns instead of 25 ns** (45.5 MHz).
2. **C2 is merged as the default** (`git merge exp/cdr-control-delay`): routed
   **12.04 ns / WNS -1.32** at the 10 ns target (A: 16.48 / -5.76; baseline:
   18.87 / -8.27), full functional suite, 105/105 regression, lint clean.
   Stability is characterised in §4.6: capture -3000..+4000 ppm, three
   frequency steps with **0 bit errors**, and a bounded 3-29 LSB limit cycle
   over 100k bits.  The one extra cycle of control latency is therefore
   accepted at the +-1000 ppm spec (2-4x margin).
3. **C2's honest position is 12-13 ns (77-83 MHz) on sky130.**  It is not a
   100 MHz closure: best WNS is -0.91 at the 12 ns target and -1.32 at 10 ns,
   and the two further cuts tried (§4.7) were rejected.  The per-architecture
   signoff tier in `tech/*/tech.yaml` stays at the **A 22 ns closure (45 MHz)**
   because C2 has not been re-run across the four PDKs at a closing
   constraint.
4. **Do not pursue B or D.**  B breaks the sampling pipeline (50-93% bit
   errors at 0 ppm) for only 9% over A; D needs FR<=6 and therefore cannot
   track +-1000 ppm.
5. **Reaching 100 MHz** needs the multiphase/DLL/phase-interpolator backend
   or a different PD/NCO partition, not more local arithmetic in
   `inc_r -> phase_next -> compare -> PD`.  The DLL phase-select prototype is
   in `rtl/cdr_multiphase.sv` and `docs/dll_backend.md`: 8 taps at 1.25 ns,
   13-bit loop add, 9.10 ns / +0.90 WNS pre-PnR at the 10 ns target, 0 errors
   over 100k bits at 0/+-1000 ppm.  Routed it is ~10.5 ns (WNS -0.46 with the
   sampler/reset treated as internal), so it recovers ~1.5 ns over C2 but does
   not close 100 MHz at ss.  The analog DLL is still a specification.
6. Records: baseline 25 ns `build/pnr/cross_pdk_close25_sky130.json`, A 22 ns
   `build/pnr/cross_pdk_close22_A.json`, C2 10 ns
   `build/pnr/cross_pdk_c2_10ns.json` (portable/local, not committed).

## 6. Reproduce

```bash
# baseline: map cdr_bang_bang with sky130 ss liberty, then STA at 10 ns
yosys -p 'read_verilog -sv rtl/cdr_bang_bang.sv; \
          read_verilog -sv rtl/cdr_sync.sv; \
          chparam -set NCO_FRAC 16 cdr_bang_bang; \
          hierarchy -top cdr_bang_bang -check; \
          synth -top cdr_bang_bang -flatten; \
          dfflibmap -liberty <sky130_fd_sc_hd__ss_100C_1v60.lib>; \
          abc -liberty <...> -D 10000; opt_clean -purge; \
          write_verilog -noattr /tmp/cdr_mapped.v'
cat > /tmp/cdr.tcl <<'EOF'
read_liberty <...>
read_verilog /tmp/cdr_mapped.v
link_design cdr_bang_bang
create_clock -name clk -period 10 [get_ports clk]
report_checks -path_delay max
report_wns
EOF
sta -no_init -no_splash -exit /tmp/cdr.tcl   # cmd_file is positional
```

A and C2 are on `master` (`rtl/cdr_bang_bang.sv`); C2 is one extra register
on the NCO control word with the sampling pipeline unchanged.  The P1/P2/B
prototypes were scratch-tree only and were **not** kept: they fail the
functional suite.  C2 is the only pipeline-style change that passes it.
