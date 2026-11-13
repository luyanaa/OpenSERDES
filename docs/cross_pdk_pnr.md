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
| icsprout55 | 25 Mb/s | 10 ns | 2372 | 4596 um2 | 1.2 uW | -3.58 ns | 13.66 ns | KLayout decks: cell LVS PASS, DRC 19 library items; full-design pending |

Hold WNS is 0 and hold violations are 0 in every corner for all four PDKs.
Route DRC (`route__drc_errors`) and antenna violations are 0 for all four.

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
- `ihp-sg13g2` has non-zero Magic/KLayout DRC and LVS errors.  The LVS report
  ends in "Top level cell failed pin matching" with the PDK's own
  `VPWR`/`VGND` design nets mapped to `VDD`/`VSS` cell power pins; both DRC
  decks report wide-metal / spacing errors while OpenROAD's own `route__drc`
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
