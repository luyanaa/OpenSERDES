# Standard-cell library selection

This note records the measured/observed trade-offs for the digital flow.  It is
not a blanket PDK signoff; every candidate must still pass the project's
DRC/LVS/STA gates.

## 1. Sky130 families

Sky130 has several foundry libraries with the same 1.8 V core devices but
different track heights, drive strengths and leakage:

| Library | Class | Relative speed | Relative area/leakage | Use |
|---|---|---|---|---|
| `sky130_fd_sc_hd` | high density, 9-track | baseline | baseline | current default; area-efficient |
| `sky130_fd_sc_hs` | high speed, 11-track | **fastest** | larger, highest leakage | speed-critical digital |
| `sky130_fd_sc_ms` | medium speed | between ls/hs | medium | speed/leakage compromise |
| `sky130_fd_sc_ls` | low speed | slower | lower leakage | low-power, non-critical |
| `sky130_fd_sc_lp` | low power | low speed | low leakage | sleep/low-power |
| `sky130_fd_sc_hdll` | high density low leakage | lower than hd | 5–10× lower leakage | leakage-sensitive |
| `sky130_fd_sc_hvl` | 5 V, 14-track | slow 5 V | large | 5 V tolerant logic only |

Official SkyWater documentation states that `hs` has the highest speed and the
highest leakage of the low-voltage families; `hd` is the density choice.

### Measured serializer comparison (TT, 1.80 V, 25 °C)

Yosys synthesis + OpenSTA, same `serializer` RTL with `CAPTURE_MODE=1`:

| Library | 10 ns clock, worst slack | 5 ns clock, worst slack |
|---|---:|---:|
| `sky130_fd_sc_hd` | +5.26 ns | +0.26 ns |
| `sky130_fd_sc_hs` | **+6.37 ns** | **+1.37 ns** |

So `hs` gives about **1.1 ns more slack** on this path at both periods.  The
price is the 11-track cell height and higher leakage.  These are TT-only
numbers; the available `sky130hs` platform does not include SS/FF libs, so do
not treat them as signoff.  The current `hd` SS result remains the signoff
baseline.

Recommendation:

- **speed first:** `sky130_fd_sc_hs`;
- **area first:** keep `sky130_fd_sc_hd`;
- **leakage first:** `sky130_fd_sc_hdll`;
- **5 V tolerant:** `sky130_fd_sc_hvl`, not a speed upgrade.

## 2. GF180MCU voltage/library comparison

The foundry 5 V libraries also contain 3.3 V timing corners.  The measured
serializer (`CAPTURE_MODE=1`, 10 ns, SS corner) is:

| Library | Voltage | SS WNS @ 10 ns | Area (µm²) | Result |
|---|---:|---:|---:|---|
| `gf180mcu_fd_sc_mcu7t5v0` | 5.0 V | **−7.496 ns** | 35,825.664 | fails |
| `gf180mcu_fd_sc_mcu9t5v0` | 5.0 V | **−3.933 ns** | 42,516.6336 | fails, best of these |
| `gf180mcu_fd_sc_mcu7t5v0` | 3.3 V | −16.934 ns | 35,825.664 | fails |
| `gf180mcu_fd_sc_mcu9t5v0` | 3.3 V | −11.318 ns | 42,516.6336 | fails |

At 10 ns all four fail the SS corner; they still rank the libraries.  The
9-track 5 V library is clearly faster than the 7-track 5 V library, at ~18.7%
more cell area.  In this design, 3.3 V operation is slower than 5.0 V for the
same library, despite the lower supply; the 5 V overdrive wins.

Recommendation:

- **speed first, validated:** `gf180mcu_fd_sc_mcu9t5v0` at **5.0 V**;
- **area first:** `gf180mcu_fd_sc_mcu7t5v0` at 5.0 V;
- 3.3 V is not a performance upgrade for this RTL; it also needs different
  IO/level-shifter/ESD planning.

## 3. OSU / OCD / AS 3.3 V libraries: DRC/LVS usability

Before using the third-party 3.3 V libraries, the cells were checked with the
GF180MCU physical flow.

| Library | Liberty corners | Site | Functional cells | DRC | LVS | Verdict |
|---|---|---|---:|---|---|---|
| OSU gp12/gp9 | TT only | custom 0.10 × 8.30 / 6.35 µm | 55 / 51 | 106/106 pass | 0 functional pass | blocked |
| OCD rt12 | nom/min/max | `unithd` 0.56 × 6.72 µm | 18 | 22/22 pass | 22/22 pass | small-block candidate |
| AS 7-track | TT/SS/FF | `unithd` 0.56 × 3.92 µm | 123 | 123/123 pass | 102 pass, 21 physical-only, 0 fail | best evaluation candidate |

### OSU `gf180mcu_osu_sc_gp12t3v3` and `gf180mcu_osu_sc_gp9t3v3`

- **DRC:** Magic `drc check` on every `.mag` cell:
  - 106/106 cells: **0 DRC errors**.
- **LVS:** Magic extraction + Netgen against the delivered `.spice`, using the
  GF180MCU-D Netgen setup:
  - 95 cells: **Netlists do not match**;
  - 11 cells: unknown/no match result;
  - 0 cells: match uniquely.

The observed cause is a device-model/process mismatch: OSU schematics use
`nfet_03p3` / `pfet_03p3`, while the D setup knows `nfet_03v3` /
`pfet_03v3`; the OSU `.mag` files also identify themselves as
`gf180mcuC`.  An alias-only Netgen attempt still failed on device parameters.

**Do not use the OSU 3.3 V cells in this flow until their model/process
mapping is fixed and LVS is clean.**  DRC-clean alone is not sufficient.

### OCD `gf180mcu_ocd_sc_rt12t3v3`

- **DRC:** 22/22 cells, 0 violations (vendor KLayout summary).
- **LVS:** 22/22 cells, **Circuits match uniquely**.

OCD is DRC/LVS clean and uses the `unithd` site (0.56 × 6.72 µm), so it is
structurally closer to a GF180 flow library.  It has only 18 functional cell
types and a per-cell Liberty set; a merged Liberty/LEF profile is still needed
before it can be used in LibreLane.  It is a candidate for small 3.3 V blocks,
not yet a general-purpose replacement.

### AS `gf180mcu_as_sc_mcu7t3v3`

`/grid/gf180mcu/gf180mcu_as_sc_mcu7t3v3` is a custom 3.3 V, 7-track library
(WiP) with a complete-looking PDK tree: consolidated TT/SS/FF Liberty, merged
GDS, LEF/TLEF (`unithd`, 0.56 × 3.92 µm), Magic layout, SPICE/CDL and Verilog.

Validation performed here:

- **DRC:** 123/123 `.mag` cells, **0 DRC errors**.
- **LVS:** 102/123 cells **match uniquely**; 21 cells are fill/tap/template
  physical-only cells with no devices; **0 functional failures**.
  Representative `inv`, `nand2`, and `dfxtp` cells were checked individually.

So AS is electrically/physically much healthier than OSU and more
flow-shaped than OCD.  Caveats:

- the upstream README labels it **WiP**;
- `RUN_CVC` is unsupported and the KLayout XOR check needs the merged GDS
  linked explicitly;
- a quick Yosys mapping of our serializer failed on the async-reset/enable
  flop type (`$_DFFE_PN0P_`); the library has `dfsrtp` but the mapping needs
  the OpenLane-compatible recipe, not a naive `dfflibmap`.

AS is therefore the best 3.3 V **evaluation** candidate, but not yet a
production-proven flow library.

## 4. Additional Sky130 and IHP libraries

### Sky130 custom libraries

`/grid/sky130` also contains:

- `sky130_as_sc_hs`: custom 0.46 × 2.72 µm (9-track) high-speed library,
  84 cells, TT/SS/FF Liberty.  README labels it WiP; it has no async-reset
  flop in the core set (only `dfxtp`/`dfxtn`/`dfxbp`/`dfxfp` and dynamic
  variants), so our async-reset RTL cannot map directly.  No delivered
  DRC/LVS report.
- `sky130_osu_sc_t12` / `sky130_osu_sc_t15`: OSU 12-track / 15-track
  libraries, 72 cells each, with TT/SS/FF and intermediate voltage corners.
  The 12T cell height is 4.44 µm, the 15T height 5.55 µm.  A Magic DRC spot
  check (`inv_1`, `nand2_1`, `dffsr_1`) gave 0 errors, but the default
  Sky130A Netgen setup did not resolve the extracted top-level pin names
  (`GND/VDD/A/Y`); a library-specific port map is needed before claiming LVS
  clean.

For now, the foundry `sky130_fd_sc_hs` remains the safest measured speed
upgrade.  The custom libraries are candidates only after their own DRC/LVS
and reset-cell strategy are resolved.

### IHP `ez130_8t_v1p1`

`/grid/ihp130/ez130_8t_v1p1` is an ETH Zurich 8-track library:

- 220 cells, 0.42 × 3.36 µm site, 1.2 V nominal;
- TT/SS/FF Liberty, LEF, GDS, CDL, Verilog;
- no delivered DRC/LVS report in the package.

Serializer comparison, TT, 1.20 V, Yosys + OpenSTA:

| Library | 10 ns slack | 5 ns slack |
|---|---:|---:|
| `sg13g2_stdcell` (9-track, current) | +5.52 ns | +0.52 ns |
| `ez130_8t_v1p1` (8-track) | **+7.67 ns** | **+2.67 ns** |

EZ130 is about **2.1 ns faster** on this path and is the obvious IHP speed
candidate.  It is not yet validated here; run the IHP KLayout DRC/LVS decks
before using it for a commit or a tapeout-oriented flow.

## 5. Conclusion

- Sky130: `sky130_fd_sc_hs` is the speed option; `hd` remains the area/signoff
  baseline.
- GF180: `gf180mcu_fd_sc_mcu9t5v0` at 5.0 V is the fastest validated option of
  the foundry libraries; mcu7 5 V is the area option.
- OSU 3.3 V: DRC clean, **LVS not clean** (model/process mismatch) — blocked.
- OCD 3.3 V: DRC/LVS clean; 18-cell, per-cell-Liberty integration needed;
  small-block candidate.
- AS 3.3 V: DRC clean; 102 functional cells LVS-clean, 21 physical-only
  unknowns, 0 failures; WiP and not yet naive-flow compatible — best 3.3 V
  evaluation candidate.
- Sky130 custom `as_sc_hs`/OSU t12/t15: need DRC/LVS and reset-cell
  integration; do not replace vendor `hs` yet.
- IHP EZ130 8T: measured +2.1 ns serializer advantage over `sg13g2_stdcell`;
  validation is the remaining gate.

## 6. Generator integration rule

A library is selectable only if it has a `scl_variants` entry with its own
Liberty corners in `tech/<pdk>/tech.yaml`.  `scripts/gen_ip.py` and the legacy
`gen_librelane_config.py` both reject an unknown `--scl`/`scl:` value instead of
silently pairing it with the default library's Liberty files.  Adding
`sky130_fd_sc_hs`, `gf180mcu_fd_sc_mcu9t5v0` or `ez130_8t_v1p1` therefore
requires a profile entry, not just a benchmark note.
