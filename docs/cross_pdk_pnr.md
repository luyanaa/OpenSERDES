# Cross-PDK placed-and-routed STA / power (P2)

`scripts/run_cross_pdk_pnr.py` generates the bang-bang RX endpoint
(`spec/bang_bang_4x.yaml`, `OVERSAMPLE=4`, 8x4 raw payload) for each supported
tech profile and runs the LibreLane `Classic` flow through to post-route STA,
power and manufacturability.  Evidence: `build/pnr/cross_pdk.json`.

## Toolchain

Run inside the project's LibreLane devshell so the whole toolchain is pinned
(`make cross-pdk-pnr` is shorthand for the command below):

```bash
cd /opt/librelane
/nix/var/nix/profiles/default/bin/nix-shell --run \
  'cd /grid/OpenSERDES && LIBRELANE_BIN=librelane \
   python3 scripts/run_cross_pdk_pnr.py \
     --spec spec/bang_bang_4x.yaml \
     --techs sky130,gf180mcuD,ihp-sg13g2,icsprout55 \
     --out build/pnr/cross_pdk.json'
```

Notes on the invocation, each of which was a hard failure before it was fixed:

- `--manual-pdk` is used for every PDK.  In ciel mode LibreLane pins each PDK
  family to its own default version hash and tries to download it when that
  exact version is not in the local ciel home; offline this aborts.  Manual mode
  uses the installed version.
- `icsprout55` is a plain `libs.ref`/`libs.tech` tree, so `--pdk-root` names
  its parent and `--pdk icsprout55-pdk` selects it.
- Design names are sanitised: `ihp-sg13g2` becomes `ihp_sg13g2` so the
  generated `DESIGN_NAME` stays a legal identifier.
- Each PDK runs at the fastest line rate its own `limits.max_sample_clock_mhz`
  allows.  gf180mcuD is capped at 50 MHz sample clock, hence 12.5 Mb/s here
  against 25 Mb/s for the others; comparing gf180 at 100 MHz would just be a
  gate violation, not a measurement.
- `RUN_IRDROP_REPORT=false`: static IR drop needs non-zero via resistance in
  the tech LEF; some plain-PDK trees declare zero and the whole flow aborts at
  that step.  IR drop is not an input to the numbers below.
- On `icsprout55` the flow now uses the PDK's LibreLane plugin and its
  documented `meta.substituting_steps`: `Odb.CheckDesignAntennaProperties`
  is removed (the Magic LEF writer is skipped, so there is no design LEF),
  `Netgen.LVS` is replaced by `ICS55.KLayoutLVS` after `OpenROAD.WriteCDL`,
  and `KLayout.DRC` runs with `run_mode=tiling`.  The current PDK working
  tree is still being repaired (its new DRC modules raise
  `'enclosing': A check value must be specified`), so the full-design
  DRC/LVS has not been re-measured; `make ics55-drc-lvs` is the bounded
  cell-level gate.  The mapped STA/power/area numbers are unaffected.
- The RTL ports `freq_trim` / `rx_freq_trim` are deliberately **not** declared
  `signed`: Yosys emits the keyword into the netlist and OpenROAD's
  `read_verilog` rejects it, which blocked STA for every design.  Consumers
  that want a signed value declare their own variable as `signed`.

## Measured results (LibreLane 3.0.17)

Target is the post-route `nom_*` corner set; power is post-route total
(internal + switching + leakage).  `CP` is the worst-corner critical-path data
arrival from `55-openroad-stapostpnr/*/max.rpt`.

| PDK | line rate | period | cells | core area | power | setup WNS | CP | DRC/LVS |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| sky130 | 25 Mb/s | 10 ns | 2186 | 15202 um2 | 3.7 uW | -8.27 ns | 18.87 ns | clean (0/0) |
| gf180mcuD | 12.5 Mb/s | 20 ns | 2915 | 48927 um2 | 49.5 uW | -8.18 ns | 29.24 ns | clean (0/0) |
| ihp-sg13g2 | 25 Mb/s | 10 ns | 2696 | 28181 um2 | 2.8 uW | -0.74 ns | 10.96 ns | magic 37893 / klayout 4739 / LVS 115 |
| icsprout55 | 25 Mb/s | 10 ns | 2372 | 4596 um2 | 1.2 uW | -3.58 ns | 13.66 ns | cell DRC 23 items / LVS PASS; full-design DRC 1117 items (cell-exclusion); full-design LVS PASS (vendor cells black-boxed) |

Hold WNS is 0 and hold violations are 0 in every corner for all four PDKs.
Route DRC (`route__drc_errors`) and antenna violations are 0 for all four.

A recent **LibreLane 3.0.14** observation of the same spec (gf180mcuD at
12.5 Mb/s, 20 ns) measured CP 27.25 ns / WNS -6.53 ns, 2901 cells and 22.9 uW,
with Magic/KLayout DRC = 0, LVS = 0, route DRC = 0 and antenna = 0.  The
3.0.17 table above remains the reference; the two tool versions are close but
not identical, so do not mix their cell/power numbers.  The DRC/LVS verdict is
unchanged: clean.

An **icsprout55** re-run after the DRC deck repair exposed a flow integration
bug: `KLAYOUT_DEF_LAYER_MAP` pointed at the PDK's GDS stream-in map
(`ics55.map`, short `M1..M5` names), while the routed DEF uses `MET1..MET5`.
KLayout therefore ignored every routing, via and pin/label layer during
streamout (the log listed `No mapping for layer 'MET1', purpose 'NET'`), so
the previous full-design DRC/LVS ran on an incomplete GDS.  The repo now
supplies `tech/icsprout55/def_layer.map` (LEF/DEF form, mapped to the official
`techfile/icsprout55.layermap`) and the cross-PDK script points
`KLAYOUT_DEF_LAYER_MAP` at it.  With that map the streamout keeps MET1-MET5,
VIA1-VIA4, T4M2 and RDL, and the GDS carries the signal pin labels.

The repaired cell gate now passes: `make ics55-drc-lvs` gives DRC rc=0 with 23
recorded items (18 categories; `--require-drc-clean` is not set) and LVS PASS.
The full-design `main` tiling DRC still does not finish with the default
full-cell checks: the FEOL `ACT_L_1` rule expands the whole CT layer by
12.5 um, grows to 1-6 GB and exceeds 25 min (BEOL alone completes; skipping
density/MOM is not enough).  With the deck's `cell_exclusion=true` mode, which
keeps the cell frames and checks only the top-level interconnect/analog
shapes, the same `main` tiling run completes in about a minute: 1117 items in
29 categories.  The cross-PDK flow now uses `cell_exclusion=true` for the
full-design DRC; `make ics55-drc-lvs` still runs the full-cell deck on
INVX1H7R.

Full-design LVS initially reached `ICS55_ERC=PASS` but
`ICS55_LVS_COMPARE=FAIL`.  The root cause is in hierarchical extraction, not
the cell GDS: the deck derives the substrate as a flat layout-extent layer
(`SUBSTRATE_EXTENT.not(NW)`) and calls `connect_global(PSUB, VSS)` /
`connect_global(NW, VDD)`.  KLayout turns every global connection into a
boundary pin on the cell circuit, so the full-design `AND2X0P5H7R` circuit
exposed ten pins (`$5 $6 $7 $8 $10 $11 VNW $I15 $I16 $I17`) instead of five.
The cell-level run passes because the cell is the top circuit.  The vendor
`_M2` cell GDS matches the PDK's `ics55_LLSC_H7CR_M2.gds` per layer (shape
count and total area); the large M2 pins are vendor-provided and unmodified.

The fix is entirely in the LVS deck (`libs.tech/lvs/ics55.lvs`, vendor cells
untouched):

1. Cell boundary normalization: pins of one cell that always land on the same
   parent net are one logical boundary; `join_nets` merges them and collapses
   the pins.  The reduction is applied only when the group count matches the
   CDL boundary, so a genuine top-level short is still reported.
2. Missing reference pins (e.g. an unconnected spare-cell output) are
   re-created from the internal net carrying the pin label.
3. CDL `_unconnected_*` dummy nets and the corresponding dangling layout
   boundary nets are disconnected on both sides.
4. Layout-only via cells (no devices) are flattened into their parents.
5. The strict-hierarchy path compares only circuits reachable from the top,
   flattens each reference cell's internal subcircuits, purges unreachable
   reference circuits, and matches cell boundaries by topology (extracted cell
   pins are numeric while CDL pins are logical).
6. `-rd blackbox=true` (used by the full-chip flow) treats the vendor cells as
   pre-qualified black boxes; the top-level interconnect is compared.  Cell
   internals are the separate `make ics55-drc-lvs` gate.

Evidence: full-design `serdes_rx_digital_nofill2_norsmp.gds` against
`merged_used2_nofill.cdl` gives `ICS55_LVS_COMPARE=PASS`, `ICS55_ERC=PASS`,
`STRICT_HIERARCHY: circuits=87, exact named module mapping and all boundary
nets constrained`, and 87/87 circuits matched.  A re-run with the committed
deck reproduces the PASS (`lvs_vendor_confirm`).  `make ics55-drc-lvs` still
passes (LVS PASS, DRC rc=0 with 23 recorded items).  Without black-boxing,
84/86 cell circuits match internally; the two exceptions are vendor-cell vs
CDL differences: INVX16H7R has the NMOS/PMOS drawn widths exchanged
(layout NMOS 3.04 um vs CDL 2.4 um, +27%), and NAND3BBX0P5H7R places the C
input at a different point of the NMOS stack.  The full-chip run black-boxes
the cells rather than masking those deviations.  The earlier OBS/LEF trim experiments and
`RT_MIN_LAYER=MET3` remain rejected.

Flow integration (current): the LibreLane `ICS55.KLayoutLVS` step now passes
`top=DESIGN_NAME` plus `strict_topology`, `strict_hierarchy` and `blackbox`.
Before that, `source(input)` selected a cell with no labels (`PORT DEPTH 0:
ports=0`) and the step reported 18268 ERC violations.  The step also treats
`ICS55_ERC=FAIL` as an LVS error.  Two flow items remain open: (a) the final
GDS carries 1138 fill/decap instances and 21 cell-pin labels on the top cell;
the deck's post-extraction cell-boundary merge aborts in KLayout 0.30
`Circuit::join_nets` on the current design's `NAND2X0P5H7R` extraction
(116 instances, 10 boundary pins), so the filled-GDS LVS is not yet runnable.
The validated full-design PASS is on the nofill GDS + filtered CDL pair.  The
experimental filled-GDS deck changes (top-label filter, pre-extraction filler
removal, open-pin handling) were reverted because they destabilized the nofill
path; a KLayout upgrade or a safe net-merge workaround is required first.
(b) `Odb.ReportDisconnectedPins` finds one disconnected top input, `rx_smp`
(0 critical), but `Checker.DisconnectedPins` is skipped because the metric is
missing; the pin should be placed or removed, and the checker metric wired up.

A **LibreLane 3.0.14** IHP observation (25 Mb/s, 10 ns) used the PDK's own
`KLayout.LVS` deck instead of netgen, to test whether the 115 netgen errors
were a pin-mapping artifact.  It measured CP 10.84 ns / WNS -0.61 ns, 2627
cells and 2.37 uW; Magic DRC 38484, KLayout DRC 4767 (all `M1.b` / `M1.e`
metal1 spacing/enclosure), route DRC 0, antenna 0.  KLayout LVS still fails
(`design__lvs_error__count = 1`): the extracted layout netlist gives four
standard cells (`sg13g2_a22oi_1`, `sg13g2_dfrbpq_1`, `sg13g2_mux2_1`,
`sg13g2_or2_1`) an extra internal pin ("No equivalent pin ... from netlist
found in reference netlist"), while each of those cells alone matches.  The
netgen result is therefore not just a netgen pin-mapping artifact; the
full-design IHP layout/extraction is not LVS-clean under either engine.  The
3.0.17 row above stays the reference; this observation is recorded in
`build/pnr/ihp_observe.json`.

## What the timing says

**No PDK closes post-route setup timing at the capability-gated target
period.**  The critical path in every run starts at
`g_bang_bang.u_cdr.freq_q[0]` — the fractional-NCO increment / loop-filter
arithmetic of the bang-bang CDR — and the required period is set by that path,
not by the deserializer or the comma aligner.

Critical-path-implied sample-clock ceiling (`1000 / CP`, an estimate from the
measured arrival time, **not** a verified closed run):

| PDK | implied Fmax |
|---|---:|
| sky130 | ~53 MHz |
| gf180mcuD | ~34 MHz |
| ihp-sg13g2 | ~91 MHz |
| icsprout55 | ~73 MHz |

This is the same NCO arithmetic structure flagged by the earlier pre-PnR
work; it does not go away after placement and routing.  Meeting 100 MHz on
this architecture needs a pipeline cut in the NCO/loop-filter path, not a
constraint tweak.

### Verified closure point (sky130)

Two confirming runs at slower line rates, same design and flow:

| line rate | period | setup WNS | hold WNS | critical path | DRC/LVS/antenna | verdict |
|---:|---:|---:|---:|---:|---|---|
| 12.5 Mb/s | 20 ns | -1.69 ns (ss) | 0 | 22.29 ns | 0 / 0 / 0 | fails |
| 10 Mb/s | 25 ns | 0 (all corners) | 0 | 24.23 ns | 0 / 0 / 0 | **closes** |

Evidence: `build/pnr/cross_pdk_close_sky130.json` (20 ns) and
`build/pnr/cross_pdk_close25_sky130.json` (25 ns).  The 25 ns run is the only
`POST_ROUTE_TIMING` + `PHYSICAL_SIGNOFF`-clean result in this set: 2212 cells,
15202 um2, 1.19 uW, Magic DRC 0, KLayout DRC 0, Netgen LVS 0, route DRC 0,
antenna 0.

Two cautions about reading these numbers:

- The critical path **grows** as the target is relaxed (18.87 ns at a 10 ns
  target, 22.29 ns at 20 ns, 24.23 ns at 25 ns) because synthesis and
  placement stop optimising once the constraint is met.  `1000 / CP` from the
  10 ns runs is therefore an optimistic ceiling, and the verified closure is
  at 40 MHz sample clock, not the ~53 MHz the table above suggests.
- 25 ns is a *closing* period found by two trial points, not a measured Fmax
  sweep.  Periods between 20 and 25 ns were not tried.

## Signoff honesty

Only **sky130** has a verified closing point that also has a clean physical
record: 45.45 MHz (22 ns, WNS 0 in all corners, Magic/KLayout DRC 0, Netgen
LVS 0); the 25 ns run closes at 40 MHz.  The other PDKs have **no closing
post-route period**: gf180mcuD at 50 MHz sample clock fails by -8.18 ns, IHP at
100 MHz by -0.74 ns (and is not DRC/LVS clean), and icsprout55 at 100 MHz by
-3.58 ns (the repaired KLayout decks pass LVS on a released cell but the full
PnR signoff is still pending).  The former 30/80/65 MHz "signoff" scalars in
`tech/*/tech.yaml` were `1000 / critical-path` estimates, not closures; they
are now recorded as `cp_implied_mhz`, with `timing_closed_mhz` and
`physical_signoff_mhz` left null.  See `limits.clock_evidence_by_arch` and
`docs/generator.md`'s third tier.


- `sky130` and `gf180mcuD` completed the full flow with Magic DRC = 0,
  KLayout DRC = 0 and Netgen LVS = 0.  These are `PHYSICAL_SIGNOFF`-clean
  implementations that **still fail** `POST_ROUTE_TIMING`.
- `ihp-sg13g2` has non-zero Magic/KLayout DRC and LVS errors.  Netgen ends
  in "Top level cell failed pin matching" with the PDK's own `VPWR`/`VGND`
  design nets mapped to `VDD`/`VSS` cell power pins; the PDK's KLayout LVS
  deck reproduces the failure with a different signature (four standard cells
  get an extra internal pin in the full-design extraction).  Both DRC decks
  report metal1 wide-metal / spacing errors while OpenROAD's own `route__drc`
  is clean.  This is a PDK signoff-deck integration gap, not a regression in
  the generated RTL.  Its STA/power/area are recorded and flagged, but this is
  **not** a signoff-clean implementation.
- `icsprout55` metrics come from `55-openroad-stapostpnr/state_out.json`.
  The KLayout DRC/LVS decks have since been repaired and are usable:
  `make ics55-drc-lvs` runs them on the released H7CR `INVX1H7R` fixture and
  records **LVS PASS** plus 19 DRC items in the library drawing (well,
  implant and density categories; see `libs.tech/drc/README.md`).  That is a
  cell-level deck check, not a full-design signoff.  The PnR flow now removes
  the Odb antenna-properties check (no design LEF) and swaps in the PDK's
  KLayout LVS step; the full-design DRC/LVS is waiting on the PDK deck
  repair.  `None` in the DRC/LVS columns means "not measured", never "zero".

## Limits (do not overclaim)

- These are post-route numbers for `serdes_rx_digital` only, at one line rate
  per PDK, one set of corners, and one set of synthesis parameters.  They are
  not a link-level BER, a silicon PPA, or a PVT/MC envelope.
- Power is post-route STA power from the PDK liberty, not measured silicon
  power and not a `collect_power.py` provenance record; it is not imported
  into `tech/ppa_measured.yaml`.
- Setup violations are results, not "unverified" runs: the flow completed and
  wrote GDS/LEF/LIB, so the numbers are recorded with the violating corners
  listed in `violations`.
- The separate jitter-tolerance result for the TX/RX PEX chain is in
  [`mixed_signal.md`](mixed_signal.md); it is a different measurement and
  must not be combined with these digital PnR numbers into a single claim.

## Current gaps (open items)

The numbered items below are **not** done.  This list is the hand-off state;
none of it is implied by the tables above.  The "Evidence handling" section is
recorded separately as intentional design policy, not an open item.

### P2 deliverable itself

1. **No PDK closes post-route timing at its capability-gated period.**  The
   critical path is the bang-bang CDR's fractional-NCO increment / loop-filter
   arithmetic (`g_bang_bang.u_cdr.freq_q[0]`).  The NCO arithmetic has since
   been tightened (exact widths + folded constants, see
   [`bang_bang_pipeline_study.md`](bang_bang_pipeline_study.md)): that change
   is landed, keeps every validated parameter, and moves the verified sky130
   closure from **25 ns to 22 ns** (45.5 MHz sample clock; WNS 0, DRC 0,
   LVS 0, `build/pnr/cross_pdk_close22_A.json`).  It also cuts routed depth by
   12.7% at the 10 ns target and area/power by 7%/20% at an equal target.
   A further change is now merged: registering only the NCO control word
   (C2, `a587565`) leaves the sampling pipeline untouched and moves the routed
   depth to **12.04 ns / WNS -1.32 at the 10 ns target** (A: 16.48 / -5.76),
   with capture -3000..+4000 ppm and 0 bit errors across +-1000 ppm steps.
   **100 MHz is still not reachable**: C2's best WNS is -0.91 at the 12 ns
   target, so its honest position is 12-13 ns (77-83 MHz), and the signoff
   tier below stays anchored to the A 22 ns closure because C2 was not
   re-run across the four PDKs at a closing constraint.  Closing 100 MHz needs
   an architectural change (multiphase/DLL/phase-interpolator backend), not a
   constraint change: the DLL phase-select prototype in
   `docs/dll_backend.md` recovers ~1.5 ns over C2; with its boundary-add
   pipeline it routed at ~10.08 ns (~99 MHz) in a matched LibreLane A/B,
   0.31 ns better than the non-pipelined core, but it is not re-run across
   the PDKs.  That is the 8-bit frequency word, which fails the +700 stress
   patterns; 10/11 bits pass them and miss timing (`dll_backend.md` 3-4).
   The digital core is integrated as `CDR_MODE=4`; the analog DLL is still a
   specification.  The tables above are the pre-A numbers and are
   kept as the baseline reference.
2. **ihp-sg13g2 is not signoff-clean.**  Magic DRC 37893, KLayout DRC 4739,
   LVS 115 errors; the LVS failure is a `VPWR`/`VGND` vs `VDD`/`VSS` power-pin
   mapping mismatch.  This is a PDK signoff-deck / flow integration gap that
   has not been diagnosed or fixed.
3. **icsprout55 DRC/LVS are unmeasured.**  Its tech LEF declares `RV`/`RDL`/`CT`
   layers Magic 8.3.623 cannot parse, so all Magic/KLayout/Netgen signoff steps
   are skipped.  Only OpenROAD-based numbers exist.  A usable Magic techfile /
   KLayout deck for that PDK is not available.
4. **25 ns is not a measured Fmax.**  It is one closing point found by two
   trials (20 ns fails, 25 ns closes); no Fmax sweep was run.
5. **Power is not provenance-backed.**  These are post-route STA power numbers
   from the PDK liberty, not `scripts/collect_power.py` evidence records, and
   are not imported into `tech/ppa_measured.yaml` / the PPA model.

### JTOL / SI

6. **The 0.45 UI tolerance is one point, not a mask.**  One channel
   (100 ohm / 16 pF), one SJ frequency (2 MHz), two PRBS-7 frames per point,
   five amplitudes.  No PVT, MC, RJ, BUJ, multi-frequency JTOL mask, BER
   vs jitter curve, or silicon correlation.  The first failure at 0.60 UI is
   an analog slicer error that the source-synchronous digital replay then
   propagates; it is not a digital CDR jitter-tolerance measurement.

### Evidence handling (design intent, not a gap)

**Measured artifacts are local only, deliberately.**  `build/` is gitignored
and the project is **portable by default**: it ships reproducible *recipes*
(`make cross-pdk-pnr`, `make pex-jtol-sweep`) rather than GDS/measurement
artifacts, and does not bind to any one LibreLane/OpenROAD/Magic/PDK version.
Users are expected to re-run signoff and characterization in their own
environment.  `build/pnr/cross_pdk.json`, the two `cross_pdk_close*.json`
records and `build/verify/pex_jtol.json` are therefore regenerated, not
delivered.  (`cross_pdk.json` in this working tree was rebuilt from run
directories after the driver process was interrupted, and says so in
`rebuilt_from`; that is a property of this local tree, not a requirement on
anyone else's.)

### Separate P2 in `docs/analog_pnr.md`

7. **Hard-macro PHY link closure remains `UNVERIFIED`.**  The soft-stdcell
   `build/verify/phy_link_closure/phy_link_closure.json` is
   `SIGNOFF_ELIGIBLE`, but `phy_link_closure_hard` fails `klayout_xor_zero`,
   `magic_drc_zero` and `klayout_drc_zero`; `hard_link` reports 71 Magic /
   12 KLayout DRC errors and `hard_link_lef_fix` stalled at
   `DRT-0418 Term u_rx_frontend/OUT has no pins on routing grid`.  Per that
   document this needs the P1-1 macro layout regeneration (interactive Magic,
   per-step extract + LVS), which has not been done.  If "P2" refers to this
   item rather than the cross-PDK work above, it is still open.
