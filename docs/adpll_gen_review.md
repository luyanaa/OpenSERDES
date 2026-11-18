# `../adpll_gen` review: what it is, what it is missing, what OpenSERDES needs

Status: read-only review of `/grid/adpll_gen` at the current checkout, plus a
check of what OpenSERDES actually consumes.  No `adpll_gen` file was modified.
Date: 2026-11-18.

## 1. Is the default PLL `../adpll_gen`?

**No, in two independent senses.**

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
| `multiphase_dco` | 100–1000 MHz | **missing** | no | 5 | multiphase, low jitter |
| `fractional_n_feedback` | 50–1000 MHz | **missing** | no | 5 | fractional-N feedback |

So: **4 of 10 are RTL-implemented, 2 are partial, 4 do not exist.**

### 2.2 Per-PDK digital ceiling (`flow/generator/pdk_profiles.json`)

Every entry carries `qualified: false`.

| PDK profile | class | `max_arch_mhz` | `signoff_mhz` | `max_ref_mhz` | maturity note |
|---|---|---:|---:|---:|---|
| ics55 | high | 836 | 479 | 244 | PEX-cap + sparse L3; no PVT/jitter signoff |
| ihp130 | high | 525 | 400 | 237 | post-route TT; SS setup not closed at nominal |
| sky130 | mid | 570 | 300 | 200 | pre-layout only; no post-route digital canary |

The remaining GF180 / ETRI profiles are `low` and are dominated by
post-route numbers in `flow/PDK_ESTIMATES.md`: gf180mcuD FD is **49.6 MHz TT /
40.8 MHz SS** with SS setup open at 20 ns.

**Consequence for OpenSERDES**: even in the best listed case (ics55,
479 MHz signoff) a 4× oversampling bang-bang CDR running at a 250 MHz sample
clock (≈1 Gb/s line rate) is above the *signoff* ceiling, and the IHP case
(400 MHz signoff) leaves almost no margin.  A 1.6 GHz complete loop is not
supported by any evidence in the repo — `README.md` §6 states this outright.

## 3. Functional gaps, by layer

### 3.1 Gaps that block using it as an OpenSERDES CDR sample clock

| # | Gap | Evidence |
|---|---|---|
| S1 | **No phase noise, no random jitter.** The only jitter numbers are *deterministic* transient period spread with noiseless sources and no routed RC. | `README.md` §9; `flow/characterization/JITTER_PHASE_NOISE.md` — `ngspice-44.2` exposes `.pss` but has no matching `.pnoise`, so PSS/phase-noise was never run |
| S2 | **No lock time, no BER, no full-top power/area.** | `README.md` §9; `flow/PERFORMANCE.md` §5 |
| S3 | **Frequency accuracy over PVT is not a ppm number.** The FLL flag is `FCW ± 1 count/REF`; `P0_A.md` says `FREQ_LOCKED=1` "must not be read as the output meets an SoC ppm budget". | `flow/P0_A.md` "Frequency metrics" |
| S4 | **Steady-state frequency is off target in the only full-lock behavioural run**: 16.5/REF vs `FCW=16`, about +3.1 %. | `README.md` §2; `flow/P0_A.md` |
| S5 | **No multiphase output.** One `OUT`, one `MID_OUT`. An OpenSERDES `cdr_multiphase` needs `2^PH_W` phases inside one sample period, and `CDR_MODE=4` needs a tap-muxed DLL. | 71-pin LEF above; `rtl/cdr_multiphase.sv` `PH_W`/`tap` ports |
| S6 | **DCO tuning is non-monotonic**: 15 non-monotonic fine steps remain in the IHP fine bank. A non-monotonic code is a functional risk for the frequency-lock loop. | `README.md` §2 and `flow/IHP130_TAPEOUT_GAPS.md` B8 |
| S7 | **KDCO map is not SAR-valid.** Median KDCO coarse −22.996 / mid −1.039 / fine −0.177 MHz/code, with **positive** slopes on mid and fine. | `flow/characterization/KDCO_JITTER_REPORT.md` |
| S8 | **`PLL_ANALOG_MACRO` is one corner** (nom 1.20 V, 25 °C) — tapeout needs TT/SS/FF. | `flow/IHP130_TAPEOUT_GAPS.md` B4 |
| S9 | **No SPEF/SDF/CDL for the analogue macro**, so no post-layout macro timing and no consumed parasitics. | `flow/IHP130_TAPEOUT_GAPS.md` B3; `macro_inventory.json` `views` has no `spef`/`sdf` |
| S10 | **Macro Liberty has no `SS_BBPD → INJ_EDGE` setup/hold arc**, which is the interface the digital loop filter samples. | `flow/IHP130_TAPEOUT_GAPS.md` B2 |
| S11 | **The full PLL top has never been assembled.** IHP top gets past PDN/placement/CTS then blocked at `OpenROAD.GlobalRouting` `GRT-0116` congestion; floorplan sizing still open. | `flow/IHP130_TAPEOUT_GAPS.md` §2.5 |
| S12 | **Digital control has two undeclared clock domains** (`DSM_CK_G`, `REF_G`), so the canary's "closed" claim covered only 4 of 6 domains. | `flow/IHP130_TAPEOUT_GAPS.md` B5 |

### 3.2 Gaps specific to each implemented architecture

**`core_a_il_dco` (300–1000 MHz, the flagship)**
- Injection-lock range, lock time and false-lock behaviour are uncharacterized
  (`README.md` §9).  `IL_NDELS=4` is fixed, so the lock range is a design
  constant nobody has measured.
- DCO frequency after PEX capacitance drops 287.85 → 178.97 MHz (−37.8 %) at
  code 8/8/8 on IHP, and PVT is 179.0 / 115.4 / 274.1 MHz (TT/SS/FF)
  (`flow/characterization/pex_ihp/README.md`).  The analogue DCO is therefore
  the binding limit, not the digital ceiling of 525 MHz.
- ISF-based phase-noise flow was never produced.

**`core_b_ring_dco` (20–300 MHz)**
- The **sky130 ring does not oscillate in the built netlist** at the old
  mapping; post-fix it reaches 43.08/27.64/13.48 MHz (FF/TT/SS) at minimum
  code — nowhere near 300 MHz.
- IHP ring at minimum code: 33.21 / 20.95 / 47.58 MHz (TT/SS/FF), so the
  practical floor is ~21 MHz and the top of the band was never measured.
- LVS uses a top-port naming waiver (`--ignore_top_ports_mismatch`), which is
  exactly the waiver OpenSERDES's IHP flow also needed — it is not a clean
  signoff.

**`core_c_counter` (2.5–26.5 MHz NCO)**
- Output resolution is the TICK quantization: at 100 MHz TICK the period is
  380/370 ns with RMS **4.985 ns** jitter.  That is a period-quantization
  artefact, unusable as a SerDes sample clock.
- It is otherwise the only architecture with a clean PVT story (ideal digital).

**`dual_loop_fll_pll` (partial)**
- `src/freq_lock.v` exists and the FLL is tested **against a stub DCO**; no
  real-DCO closed-loop acquisition or reacquisition measurement exists.
- Reacquisition is "FSM-level tests only" (`P0_A.md`).

**`fractional_n_dsm` (partial)**
- `src/delta_sigma.v` passes its saturation/width tests, but fractional spurs
  and in-loop phase noise were never measured, and the block was never run
  inside a locked loop.
- Odd/even ratio handling is a known clock-PHY issue: the raw `OUT_DIV` edge
  spacing is non-uniform, which is why `DDR_CLOCK_PHY` had to avoid the
  one-period DLL path (`flow/DDR_CLOCK_PHY_PATH.md` §2).

### 3.3 Gaps in the adjacent blocks OpenSERDES would actually want

These matter more than the PLL gap, because `CDR_MODE=4` is a *DLL phase
select*, not a frequency synthesis problem.

**`ADDLL_TOP` / `dll_dcdl` — closest analogue to OpenSERDES `CDR_MODE=4`**
- The old `ADDLL_TOP` **false-locked at half period**: `LOCKED=1` with a
  0.843 ns edge offset on a 4.870 ns period and 41.4 % output duty.  It is no
  longer instantiated; the fixed path uses balanced `~CLK_IN/~CLK_OUT`, a
  captured PD code and a DLF clocked on `!CLK_IN`, giving `DLY_CODE=34` and
  phase 0 mod period at 4.88 ns.
- Real ics55 DCDL range is **4.71–5.55 ns TT, 6.43–7.98 ns SS, 3.97–4.52 ns
  FF** — a 5 ns target is blocked at SS and FF.  The delay line, not the
  control loop, is the wall.
- The DLL path only closes with a **uniform** reference; a fractional-divider
  clock has no valid one-period code and the loop correctly refuses to lock.

**`ADCC_TOP` (duty-cycle corrector)**
- Verified 30 % → 49.6 % at 5 ns, but biased: ≈55 % at 4 ns
  (`flow/DDR_CLOCK_PHY_PATH.md` §3).
- **Open physical defect**: a parasitic second pulse appears when `fall_d`'s
  delay is comparable to the low period — the OR recombine re-exposes the
  previous high pulse.  Silicon needs a pulse-swallow/SR-latch combiner plus
  tap updates frozen to a quiet boundary.

**`CDR_TOP` (Alexander bang-bang over a DCDL)**
- Only a **sky130** macro exists (`flow/macro/cdr_sky130`); nothing on IHP.
- Samplers are now real FFs (`dfrtp` on `posedge rec_clk`, `dfrtn` on
  `negedge rec_clk`), but the 0.5-UI edge offset is the half-rate falling edge
  — "the stdcell-only approximation of a matched 0.5-UI delay", not a matched
  second tap.
- The Alexander sign/phase mapping (early/late vs `code` direction) is only
  validated to "the loop converges".
- The lock detector asserts on `PHASE_CODE` dither within ±4; it does not
  prove the eye is centred.

## 4. What OpenSERDES needs, and the mapping

OpenSERDES's CDR family (`rtl/cdr_bang_bang.sv`, `rtl/cdr_multiphase.sv`,
`CDR_MODE=4` DLL phase select) imposes four requirements on a clock source:

| Requirement | Why | Best `adpll_gen` candidate | Gap |
|---|---|---|---|
| Sample clock at `OS × line rate` | bang-bang / fixed-oversampling resample the eye | `core_a_il_dco` `OUT`, divided by `SET_DIV` | S1, S2, S3, S6, S11 |
| Controllable **multi-phase** taps inside one sample period | `cdr_multiphase` `PH_W`-bit `tap`, and `CDR_MODE=4` | `ADDLL_TOP` / `dll_dcdl` | DCDL range blocks 5 ns at SS/FF (above) |
| ppm-level long-term accuracy | `adaptive_oversampling` is auto-selected whenever `rx.ppm != 0` | fractional-N (`fractional_n_dsm`) | partial: no spur/noise measurement |
| Jitter budget the link can be signed off against | any CDR jitter-tolerance claim | — | **nothing exists** (S1) |

Two structural mismatches are worth stating plainly:

- **`adpll_gen` produces no multiphase output.**  Its PLL gives one `OUT` and
  one `MID_OUT`; multiphase is a *catalog entry that does not exist*
  (`multiphase_dco`, implementation `missing`).  So it cannot feed
  `cdr_multiphase` at all today — that path needs the DLL, not the PLL.
- **The PLL and the CDR compete for the same job.**  OpenSERDES's
  `cdr_multiphase` already contains a 13-bit phase accumulator and its own
  frequency trim; `CDR_MODE=4` already contains a DLL phase mux.  Wiring
  `adpll_gen`'s PLL in would add a second frequency loop with an
  unquantified jitter contribution, on top of a CDR that already tracks ppm.

## 5. Verdict

- `adpll_gen` is an **implementation-characterization** project, by its own
  README.  It is not a product signoff, and no layer of it is jitter/phase
  qualified.
- Of ten catalogued architectures, **four are implemented, two partial, four
  missing**.  Every PDK profile is `qualified: false`.
- Its PLL is usable as a **frequency-planning reference and a macro-timing
  research artifact**, not as the OpenSERDES default clock source.
- Its most transferable piece for OpenSERDES is not the PLL but the **DLL/DCDL
  and ADCC work**, because `CDR_MODE=4` is a phase-select problem.  That work
  is also where the single hardest measured wall sits: the ics55 DCDL cannot
  reach 5 ns at SS/FF.
- The single most valuable missing measurement, for both projects, is a
  **phase-noise / random-jitter number**.  Neither repo has one; ngspice-44.2
  in this environment has no `.pnoise` to produce it.

### Recommended stance for OpenSERDES

Keep the current fail-closed behaviour (`clock_source` limited to
`source_sync` / `independent`) and keep PLL backends out of
`CAPABILITY_INTEGRATED` until a phase-noise number exists.  Document the
external-PLL contract instead — sample-clock frequency with `OS` factor,
ppm accuracy, integrated jitter over the CDR loop bandwidth, and (for
`cdr_multiphase`) the number of phases and their matching — so an external
PLL can be qualified against it without OpenSERDES having to own the silicon.
