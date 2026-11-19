# `../adpll_gen` review: what it is, what it is missing, what OpenSERDES needs

Status: read-only review of `/grid/adpll_gen`, refreshed against commit
`222b70f` (2026-11-19 09:26).  No `adpll_gen` file was modified.  Two of the
findings from the 2026-11-18 pass of this document are **withdrawn** because
`adpll_gen` closed them in the meantime; those are called out explicitly in
§6 rather than silently edited away.

Verification note: every claim below was checked against the checkout on this
date.  Where a test exists and can be built, it was **run from OpenSERDES**
rather than trusted from the other repo's own report; those runs are marked
"independently reproduced".

## 1. Is the default PLL `../adpll_gen`?

**No, in two independent senses.  Unchanged since the last review.**

1. **OpenSERDES does not reference `adpll_gen` at all.**  `rg -rn adpll_gen`
   over the repo (excluding `build/`) returns nothing.  There is no PLL, DCO or
   DLL RTL in `rtl/` — `ls rtl | rg -i 'pll|dco|dll|adpll'` is empty.
2. **The generator fails closed on every physical clock source.**
   `scripts/gen_ip.py` accepts the *vocabulary* `external_reference`,
   `ring_pll`, `adpll`, `dll` in the `clock_source` enum, but
   `CAPABILITY_INTEGRATED["clock_source"]` is only
   `{"source_sync": True, "independent": True}`.  `rx.clocking` is separately
   limited to `independent | source_sync | auto`.  So a spec that asks for
   `adpll` is rejected twice, before any file is emitted.

The enum entries are a roadmap, not a claim of support — `docs/generator.md`
says so explicitly.  `spec/default.yaml` uses `clock_source: independent`.

## 2. What `adpll_gen` actually contains

Analogue macro: `PLL_ANALOG_MACRO` (DCO + injection chain + SS-BBPD sampler).
Its LEF is 160 × 105 µm (0.0168 mm²), Liberty `area = 7865.385` µm²,
**71 pins**: `COARSE_MUX[15]`, `MID_MUX[15]`, `FINE_MUX[31]` (61 control pins),
`SS_BBPD[2]`, `INJ_EDGE`, `INJ_EN`, `REF`, `OUT`, `MID_OUT`, `DCO_EN`,
`VDD`, `VSS`.

Note what is **not** in that pin list: `FCW`, `SET_DIV`, `OUT_DIV`, `CK`,
`LOCKED`, `ERR`.  All of the digital control lives in a separate
`pll_logic` block, so the macro is not a PLL — it is the frequency-generating
half of one.

### 2.1 Architecture catalog (`flow/generator/architecture_catalog.json`)

| id | claimed range | implementation | hard macro | complexity | features |
|---|---|---|---|---|---|
| `core_a_il_dco` | 300–1000 MHz | `rtl` | yes | 4 | integer-N, injection-locked |
| `segmented_il_dco` | 300–1500 MHz | **missing** | no | 5 | integer-N, IL, wide range, segmented |
| `core_b_ring_dco` | 20–300 MHz | `rtl` | yes | 2 | integer-N, ring |
| `core_c_counter` | 2.5–26.5 MHz | `rtl` | yes | 1 | integer-N, counter |
| `hybrid_dco` | 50–500 MHz | **missing** | no | 4 | ring + IL |
| `dual_loop_fll_pll` | 50–1000 MHz | `rtl_partial` | yes | 4 | dual loop, fast lock |
| `fractional_n_dsm` | 50–1000 MHz | `rtl_partial` | yes | 5 | integer-N, DSM |
| `low_res_tdc_bangbang` | 10–200 MHz | **missing** | no | 2 | bang-bang, low-res TDC |
| `multiphase_dco` | 100–1000 MHz | **`missing` — but the RTL exists, see §3.1** | no | 5 | multiphase, low jitter |
| `fractional_n_feedback` | 50–1000 MHz | **missing** | no | 5 | fractional-N feedback |

So: **4 of 10 are RTL-implemented, 2 partial, 4 do not exist** — with one
important correction: `multiphase_dco` is listed as `missing` with zero
`implementation_refs`, yet `src/pll_multiphase_dco.v` was committed in `5b83059`
and its test passes.  The catalog is **stale for that entry**, which matters
because the catalog is what the generator consults.

### 2.2 Per-PDK digital ceiling (`flow/generator/pdk_profiles.json`)

Every entry still carries `qualified: false`.

| PDK profile | class | `max_arch_mhz` | `signoff_mhz` | `max_ref_mhz` | maturity note |
|---|---|---:|---:|---:|---|
| ics55 | high | 836 | 479 | 244 | PEX-cap + sparse L3; no PVT/jitter signoff |
| ihp130 | high | 525 | 400 | 237 | post-route TT; SS setup not closed at nominal |
| sky130 | mid | 570 | 300 | 200 | pre-layout only; no post-route digital canary |

The remaining GF180 / ETRI profiles are `low` and dominated by post-route
numbers in `flow/PDK_ESTIMATES.md`: gf180mcuD FD is **49.6 MHz TT / 40.8 MHz SS**
with SS setup open at 20 ns.

**Consequence for OpenSERDES**: even the best listed case (ics55, 479 MHz
signoff) is above the ceiling a 4× oversampling bang-bang CDR needs for
≈1 Gb/s (250 MHz sample clock) only by 1.9×, and IHP (400 MHz) by 1.6×.  A
1.6 GHz complete loop is not supported by any evidence in the repo —
`README.md` §6 states this outright, unchanged.

## 3. What changed since the 2026-11-18 review, and what it means

Eight commits landed.  Three of them matter to OpenSERDES.

### 3.1 `5b83059` — the multiphase DCO and an IHP CDR backend exist now

This is the single most important change for us, because `CDR_MODE=4` needs a
multi-phase sample clock and the previous review said none existed.

`src/pll_multiphase_dco.v` taps a **dedicated in-loop delay line** rather than
`PLL_ILDCO`'s `BASE_CHAIN` (that chain only covers part of a period and its
delay moves with the tuning code, which would break a uniform grid).  Two
design points that took iteration, both relevant to anyone reusing it:

- The line must have an **odd** stage count to oscillate and must span a
  **full** period, so `NSTAGE = (1<<DELAY_BITS)|1` and taps sit at
  `floor(k*NSTAGE/NPHASE)` — an odd line cannot have an exact integer stride,
  so uniformity is approximate by construction.
- The stage cells are `(* keep *)(* dont_touch *)`, because ABC otherwise
  collapses the line — the same hazard that silently shortened the ring DCO.

`flow/macro/cdr_ihp130/` is the CDR back end for `CDR_MODE=4`: multiphase DCO,
a glitch-managed phase mux that parks `REC_CLK` low on a tap change, and the
sampler hand-off.  The BBPD, loop filter and lock FSM deliberately stay in
`cdr_multiphase`; duplicating them would put two loops on one tap.

**Independently reproduced from OpenSERDES** (both compile to iverilog with the
CIEL `sg13g2_stdcell.v` + `sg13g2_udp.v`):

```text
pll_multiphase_dco_tb : PASS  (PH_W=3 -> 8 phases, taps uniform within 0.4%)
cdr_top_tb            : PASS  (8 phases uniform, DCO oscillates, REC_CLK parked
                               low on tap change, sampler tracks DATA)
```

This means the "no multiphase output" gap in the previous review is **closed at
RTL level**.  What remains is §4's S5': the catalog does not know about it, and
`cdr_ihp130` has never been through PnR or DRC/LVS.

### 3.2 `222b70f` — the DLL code→delay map is monotone now (DNL 0)

This closes a defect that would have broken a one-period DLL phase select: the
segmented line had a fixed bypass `EXTRA_PS=62 ps` at every segment boundary,
which made each boundary step 92 ps = **3.07 LSB** against a 30 ps in-segment
step.  Code `k*56` lands on a node that already contains k full segments, so any
nonzero bypass is pure DNL.  `EXTRA_PS` is now 0, and the measured result over
all 280 codes is **0.00 LSB worst DNL, no defect**, with a 5 ns reference
hitting code 69 at exactly 5000 ps (previously code 67 at 5002 ps, with gaps
that left some periods with no legal code at all).

`a9f51f7` fixed three prerequisites in the same area, and two of them are
lessons worth carrying into OpenSERDES' own DLL/`CDR_MODE=4` work:

- **A posedge-only divider cannot give 50 % duty for an odd divisor.**  div=3 was
  2:1 (66.7 %), div=5 3:2 (60 %), div=15 8:7 (53.3 %).  A DLL fed from that sees
  unequal phase steps.  The fix is the standard two-counter construction (one
  per clock edge, OR-ed), which is exactly 50 %.  If OpenSERDES ever feeds a
  phase-select DLL from a divider, the divider's duty is part of the contract.
- **Freezing tap commits to a "quiet window" can deadlock.**  Once the delay
  exceeds the low half-period there is no quiet window at all.  The working rule
  is to freeze the commit to a boundary **every period is guaranteed to reach**
  (here the rising input edge).

**Caveat that must not be dropped:** these 5 ns closures are behavioural,
simulated at the STA corners.  `src/dll_full_period_tb.v` says so itself —
*"These are behavioral numbers from the STA range, not silicon."*  The measured
ics55 DCDL range (TT 4.71–5.55 ns, SS 6.43–7.98 ns, FF 3.97–4.52 ns, so 5 ns is
blocked at SS and FF) is a **device-level** result and is not overturned by the
behavioural closure.  Both statements are true at once: the control loop can now
reach every code the line has, and the line itself still cannot hit 5 ns at SS.

### 3.3 `c0c488e` / `c087a46` — macro SPEF/SDF and a real IHP clock gate

- The macro now ships `.spef` and `.sdf`, and both were checked end to end
  rather than for existence: OpenROAD back-annotates the SPEF against the routed
  netlist and macro post-PEX STA at TT gives setup **+1.4837 ns** / hold
  **+0.5937 ns**; the SDF annotates in an Icarus gate sim and the DCO produces
  130 `OUT` edges in 200 ns.
- `PLL_USE_IHP_ICG` builds the gate from `sg13g2_dllrq_1` (the library latch)
  plus an AND, because the PDK's actual gating cells (`sg13g2_lgcp_1`/`slgcp_1`)
  are marked `dont_use` **and** listed in `synth_exclude.cells`, so they cannot
  be used.  The latch is instantiated directly because `dfflibmap` does not map
  inferred latches; an `always @(*) if (!CLK)` form left it unmapped.

## 4. Functional gaps that remain

### 4.1 Gaps that block using it as an OpenSERDES CDR sample clock

| # | Gap | Evidence |
|---|---|---|
| S1 | **No phase noise, no random jitter.** Only *deterministic* transient period spread exists, with noiseless sources and no routed RC. Peak-to-peak numbers are not a jitter budget. | `README.md` §9; `flow/characterization/JITTER_PHASE_NOISE.md`. `ngspice-44.2` here exposes `.pss` but has no matching `.pnoise`, so PSS/phase noise has never been run |
| S2 | **No lock time, no BER, no full-top power/area.** | `README.md` §9; `flow/PERFORMANCE.md` §5 |
| S3 | **Frequency accuracy over PVT is not a ppm number.** The FLL flag is `FCW ± 1 count/REF`; the docs explicitly say `FREQ_LOCKED=1` "must not be read as the output meets an SoC ppm budget". | `flow/P0_A.md` "Frequency metrics" |
| S4 | **Steady-state frequency is off target** in the only full-lock behavioural run: 16.5/REF vs `FCW=16`, about **+3.1 %**. Unchanged. | `README.md` §2; `flow/P0_A.md` |
| S5' | **The multiphase bits exist in RTL but are not registered or signed off.** `architecture_catalog.json` still says `implementation: missing` / `refs: []` for `multiphase_dco`; `flow/macro/cdr_ihp130/` has **no `macro.sdc.tcl`** (compare `cdr_sky130`, which has one) and **no PnR run, no DRC/LVS, no SIGNOFF.md**. | catalog dump; `ls flow/macro/cdr_ihp130` vs `cdr_sky130` |
| S6 | **DCO tuning is non-monotonic**: 15 non-monotonic fine steps remain in the IHP fine bank. Note this is a *different* defect from the DLL DNL that `222b70f` fixed — that one was the delay line, this one is the DCO tuning bank. | `README.md` §2; `flow/IHP130_TAPEOUT_GAPS.md` B8 |
| S7 | **KDCO map is not SAR-valid.** Median KDCO coarse −22.996 / mid −1.039 / fine −0.177 MHz/code, with **positive** slopes on mid and fine. | `flow/characterization/KDCO_JITTER_REPORT.md` |
| S8 | **The macro's default Liberty is still single-corner**, even though per-corner views now exist under `corners/`. `PLL_ANALOG_MACRO.lib` at the macro root is `nom 1.20 V / 25 C` only; the three-corner set is `corners/PLL_ANALOG_MACRO__{tt_1p20V_25C,ss_1p08V_125C,ff_1p32V_m40C}.lib`. `config.macro.yaml` maps corners correctly, so this is a packaging nit, not a blocker. | `ls flow/macro/ihp130_sg13g2/*.lib` vs `corners/*.lib` |
| S9 | ~~No macro SPEF/SDF~~ **CLOSED** by `c0c488e`, and verified usable, not just present. | §3.3 |
| S10 | **The macro Liberty still has no `SS_BBPD → INJ_EDGE` setup/hold arc.** All three corner libs have `setup_rising: 0` and `hold_rising: 0`; the 42 `timing()` groups are all combinational. `SS_BBPD` exists only as a `bus_type`, with no timing to `INJ_EDGE`. This is the interface the digital loop filter samples, so top-level STA still checks that capture against nothing. | `flow/IHP130_TAPEOUT_GAPS.md` B2; verified by grepping all three `corners/*.lib` |
| S11 | **The full PLL top is assembled and routes, but does not close setup.** See §4.3 — this is the biggest change from the previous review, where the top had never been built. | §4.3 |
| S12 | **Two undeclared clock domains were the symptom, not the disease** — they are now declared (`DSM_CK_G`, `REF_G`), and declaring them immediately exposed a real −6.14 ns DSM path that had been invisible. See §4.3. | `flow/IHP130_TAPEOUT_GAPS.md` §2.6 |

### 4.2 Gaps specific to each implemented architecture

**`core_a_il_dco` (300–1000 MHz, the flagship)**
- Injection-lock range, lock time and false-lock behaviour are still
  uncharacterized.  `IL_NDELS=4` is fixed, so the lock range is a design
  constant nobody has measured.
- DCO frequency after PEX capacitance drops 287.85 → 178.97 MHz (−37.8 %) at
  code 8/8/8 on IHP; PVT is 179.0 / 115.4 / 274.1 MHz (TT/SS/FF).  The analogue
  DCO is the binding limit, not the 525 MHz digital ceiling.
- ISF-based phase-noise flow was never produced.

**`core_b_ring_dco` (20–300 MHz)**
- The **sky130 ring does not oscillate in the built netlist** at the old
  mapping; post-fix it reaches 43.08/27.64/13.48 MHz (FF/TT/SS) at minimum
  code — nowhere near 300 MHz.
- IHP ring at minimum code: 33.21 / 20.95 / 47.58 MHz (TT/SS/FF), so the
  practical floor is ~21 MHz and the top of the band was never measured.
- LVS uses a top-port naming waiver (`--ignore_top_ports_mismatch`), the same
  kind of waiver OpenSERDES' own IHP flow needed.  It is not a clean signoff.

**`core_c_counter` (2.5–26.5 MHz NCO)**
- Output resolution is TICK quantization: at 100 MHz TICK the period is
  380/370 ns with RMS **4.985 ns** jitter — a period-quantization artefact,
  unusable as a SerDes sample clock.

**`dual_loop_fll_pll` (partial)**
- `src/freq_lock.v` exists and the FLL is tested **against a stub DCO**; no
  real-DCO closed-loop acquisition or reacquisition measurement exists.
- Reacquisition is "FSM-level tests only".

**`fractional_n_dsm` (partial)**
- `src/delta_sigma.v` passes its saturation/width tests, but fractional spurs
  and in-loop phase noise were never measured, and it has never run inside a
  locked loop.
- The DSM **error-feedback adder chain does not fit one `DSM_CK` period**:
  −6.14 ns at SS on `dff_err3[11] → FINE_MUX_D[14]`, 101 of 181 paths.  Closing
  it needs an RTL change (pipeline the error feedback, narrow `BITS_DSM`, or
  register the FINE_MUX decode), not a PnR knob.

### 4.3 The IHP top: assembled and routed, but setup is open

This is the substantive change from the last review, and it is worth stating
precisely because three separate things got confused in the earlier record.

`RUN_2026-11-18_17-29-18` was the **first** time the digital control and the
analogue macro existed in one netlist, and it completed synth → floorplan →
place → CTS → global route → detailed route → antenna (0/0) → RCX → post-PnR
STA.  Congestion took three attempts and the cause was **pin access, not area**:
30 of the macro's 71 pins sit on the LEF's left edge and 37 on the right, so at
(0,0) half of them landed on the die boundary with no access.  At (40,40) it
routes clean.  Die 329584 µm², 22900 instances, 22.5 % utilization.

The first STA number (−1.0090 ns SS) was an **SDC modelling bug**, not a design
problem: `OUT_DIV`/`CK` are divided *clock* outputs but were constrained with
`set_output_delay`.  After syncing the three ics55 SDC fixes (they are generated
clocks; `SET_DIV`/`LOAD_DIV` are static straps; CDC hold exemption), that path
disappears entirely and max_slew/max_cap are 0/0 at all three corners.

What is left after the SDC fix, per-corner, from the routed database:

| corner | Hold WNS | Setup WNS | Setup TNS | max_slew | max_cap |
|---|---:|---:|---:|---:|---:|
| TT 1.20 V/25 C | −0.0349 | −3.1034 | −97.85 | 0 | 0 |
| SS 1.08 V/125 C | −0.0063 | −6.1426 | −246.96 | 0 | 0 |
| FF 1.32 V/−40 C | −0.0461 | −1.2775 | −35.16 | 0 | 0 |

Two distinct remaining causes, neither of which is a PnR knob:

- **DSM domain setup** (−6.14 ns SS, 101 of 181 paths): the `delta_sigma`
  error-feedback adder chain reaching the FINE_MUX register inside one `DSM_CK`
  period.  Needs RTL work (§4.2).
- **A clock-gating hold check** (−0.0349/−0.0063/−0.0461 ns TT/SS/FF) on
  `u_fll_icg.en_l → AND2 → REF_G`.  This is a *side effect of declaring*
  `REF_G_CLK`: without a PDK gating cell the gate is a plain AND, so the tool
  treats the combined enable as a gating signal and checks it.  Two ways out —
  map to the PDK gating cell, or pin the gate open for hold.  Single-digit
  picoseconds, but it must not be silently waived.

A separate `sta_per_corner.tcl` path was needed because LibreLane's bundled
`sta` could not finish 22900 instances × 9 library loads within 15 min/corner.
On the same database it reports hold **positive at every corner**, including the
clock-gate check at +9.5972/+9.5110/+9.6472 ns.  That is not a contradiction of
the −0.0349/−0.0063/−0.0461 ns above: those numbers were measured on the
**fallback negedge-DFF gate** (an inferred `if (!CLK)` structure), and `c087a46`
replaced it with a directly instantiated `sg13g2_dllrq_1` latch plus an AND.
The hold check closes once the gate is a real latch, which is why `c087a46`
exists.  What is still open is only that a PDK gating cell
(`sg13g2_lgcp_1`/`slgcp_1`) cannot be used at all — it is `dont_use` and
`synth_exclude.cells` — so the latch+AND structure is the permanent answer for
this library, not a stopgap.

## 5. What OpenSERDES needs, and the mapping

OpenSERDES' CDR family (`rtl/cdr_bang_bang.sv`, `rtl/cdr_multiphase.sv`,
`CDR_MODE=4` DLL phase select) imposes four requirements on a clock source:

| Requirement | Why | Best `adpll_gen` candidate | Gap |
|---|---|---|---|
| Sample clock at `OS × line rate` | bang-bang / fixed-oversampling resample the eye | `core_a_il_dco` `OUT`, divided by `SET_DIV` | S1, S2, S3, S6, S11 |
| Controllable **multi-phase** taps inside one period | `cdr_multiphase` `PH_W`-bit `tap`, and `CDR_MODE=4` | `pll_multiphase_dco` + `cdr_ihp130` | **RTL exists and its TB passes; no PnR/DRC/LVS and not in the catalog** (S5') |
| ppm-level long-term accuracy | `adaptive_oversampling` is auto-selected whenever `rx.ppm != 0` | fractional-N (`fractional_n_dsm`) | partial; no spur/noise measurement, DSM path does not close |
| Jitter budget the link can be signed off against | any CDR jitter-tolerance claim | — | **nothing exists** (S1) |

Two structural mismatches remain worth stating plainly:

- **The PLL and the CDR compete for the same job.**  OpenSERDES'
  `cdr_multiphase` already contains a 13-bit phase accumulator and its own
  frequency trim; `CDR_MODE=4` already contains a DLL phase mux.  Wiring
  `adpll_gen`'s PLL in would add a second frequency loop with an unquantified
  jitter contribution, on top of a CDR that already tracks ppm.
- **What is genuinely complementary is the multiphase DCO**, not the PLL.
  `pll_multiphase_dco` solves exactly the problem `CDR_MODE=4` has — a uniform
  phase grid — and `cdr_ihp130` already implements the "park REC_CLK low on tap
  change" rule that OpenSERDES' own DLL path needs.  That is the piece worth
  integrating, and it is the piece with the least signoff behind it.

## 6. Corrections to the previous revision of this document

Recorded rather than quietly edited, because the earlier version is what shaped
the integration plan:

1. **"`multiphase_dco` does not exist, so it cannot feed `cdr_multiphase` at
   all"** — **withdrawn.**  `src/pll_multiphase_dco.v` exists and its test
   passes (independently reproduced).  The *catalog* is what said `missing`,
   and the catalog is stale.  The real gap is that the new RTL and the new
   `cdr_ihp130` macro have no PnR, DRC/LVS, SDC or signoff (§4.1 S5').
2. **"No SPEF/SDF for the analogue macro"** — **closed** in `adpll_gen`
   (`c0c488e`), and verified usable in both directions (§3.3).
3. **"Zero multi-corner macro Liberty"** — **closed**; three per-corner views
   exist under `corners/`.  What remains is only that the root-level
   `PLL_ANALOG_MACRO.lib` is still TT-only (S8).
4. **"The top level has never been assembled"** — **closed**; it is assembled,
   routed and reaches post-PnR STA (§4.3).  The correct current statement is
   "assembled and routed, setup open at −3.10/−6.14/−1.28 ns TT/SS/FF, cause
   identified as a DSM RTL path plus a clock-gating hold check".
5. **Unchanged and still the headline**: no phase-noise or random-jitter number
   exists in either project, `ngspice-44.2` here has no `.pnoise`, the DCO fine
   bank still has 15 non-monotonic steps, and every PDK profile is
   `qualified: false`.

## 7. Verdict

- `adpll_gen` is an **implementation-characterization** project by its own
  README.  Nothing in it is jitter/phase-noise qualified, and that has not
  changed across eight commits of real progress.
- The progress is real and concentrated in the **DLL/phase-select** area, which
  is precisely where OpenSERDES' `CDR_MODE=4` lives: DNL 0 on the delay line,
  50 % duty dividers, a deadlock-free commit rule, and a multiphase DCO whose
  test passes.
- Its PLL remains usable as a **frequency-planning reference and a macro-timing
  research artifact**, not as the OpenSERDES default clock source.
- The most valuable missing measurement, for both projects, is still a
  **phase-noise / random-jitter number**.

### Recommended stance for OpenSERDES

1. Keep `clock_source` fail-closed at `source_sync` / `independent` until a
   phase-noise number exists.
2. Define the **external PLL contract** (sample-clock frequency with `OS`
   factor, ppm accuracy, integrated jitter over the CDR loop bandwidth, and for
   `cdr_multiphase` the phase count and phase-matching tolerance) so an external
   PLL can be qualified against it without OpenSERDES owning the silicon.
3. If `CDR_MODE=4` is pursued, take `pll_multiphase_dco` + the `cdr_ihp130`
   mux/hand-off **as a starting point to re-verify in our own flow**, not as a
   qualified block: it has no DRC, no LVS, no SDC and no `SIGNOFF.md`.  Also
   carry over the two hard-won rules from `a9f51f7` verbatim — odd-divisor duty
   must be 50 % before a DLL sees it, and a tap commit must freeze to a boundary
   every period reaches, never to a "quiet window".
