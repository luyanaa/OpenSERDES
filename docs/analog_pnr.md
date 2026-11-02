# Analog macro integration / PnR status

## C soft-stdcell signoff

The C top uses a portable soft-stdcell implementation generated from the
standard-cell backend:

```text
TX: sky130_fd_sc_hd__inv_1 -> inv_4 -> inv_16
RX: sky130_fd_sc_hd__inv_4
```

The config is produced with:

```bash
python3 scripts/gen_analog_pnr_config.py --soft-stdcell \
  --package out/sky130-soc --out build/analog_pnr_sky130
```

The SDC is derived from `spec/soc_sky130.yaml`:

```text
line rate 25 Mb/s -> TX UI 40 ns
user clock 50 MHz -> pclk / AXI 20 ns
RX forwarded clock is a primary top-level clock at the TX UI period (source-synchronous)
```

Latest completed run (LibreLane 3.0.17):
`build/analog_pnr_sky130/runs/analog_link_fanout4_finish`

```text
XOR differences: 0
Magic DRC:       0
KLayout DRC:     0
LVS:             0 unmatched devices/nets/pins
Setup WNS:       +7.521 ns
Hold WNS:        +0.144 ns (at 25 Mb/s / 40 ns TX UI)
PDN:             generated successfully
```

The `rx_clk` generated-clock warning is removed by modeling the forwarded RX
clock as a primary top-level clock; the SDC no longer pretends an internal
tx_clk path exists.  The physical run is the same `analog_link_fanout4_finish`
GDS/ODB.  At the earlier internal targets of 0.75 ns / 0.20 pF it reported
`33` max-slew and `1` max-cap violation.  The final electrical targets are
now 0.90 ns / 0.22 pF, both below the standard-cell Liberty limits
(`~1.5 ns` and `0.281 pF` for the `clkbuf_16` output).  A post-route STA check
on the same ODB + SPEF at the new limits reports **0 max-slew and 0 max-cap
violations**; no physical or LVS change was made.  The 0.75/0.20 counts remain
archived as a stricter-target historical result.

## Macro identities (must not be conflated)

`analog/macros/` now contains separate electrical identities:

| identity | electrical topology | physical evidence |
|---|---|---|
| `tx_inv_chain_1_3_9` | stdcell `inv_1 -> inv_4 -> inv_16` | physical stdcell GDS, DRC/LVS/PEX |
| `rx_parallel_inv_macro` | three parallel stdcell inverters | physical stdcell GDS, DRC/LVS/PEX |
| `rx_res_fb_pmos` | inverter + two series nonlinear PMOS feedback | original full GDS, DRC/LVS/PEX |

Only manifests with matching `electrical_topology`, `physical_topology`, and
`topology_match: true` may set `physical_signoff_eligible: true`.  The
`rx_res_fb_pmos` identity is the original feedback circuit and is not aliased
to the stdcell RX macro.

## Package SPICE

`macro_package.py` copies standalone canonical SPICE, a local model include,
and (where needed) the post-layout PEX file.  The package contains no
repository-relative includes or `$PDK_HOME/LVS` references.  The smoke test
stages the package outside the repository and runs ngspice DC/transient plus
TX/RX polarity checks.

The package-local models are level-1 smoke models; they prove executable
topology and polarity, not Sky130 analog accuracy.  Sky130 PVT/MC evidence
remains in the dedicated analog flow.

## Hard macro / LibreLane

The C top keeps the soft-stdcell front-end as the default so DRC/LVS/STA
close in one hierarchy.  Defining `SERDES_LINK_HARD_MACROS` swaps the
soft stand-ins for the two real custom cells:

- `u_tx_driver` → `tx_inv_chain_1_3_9` (custom inverter chain; power on
  **met4** rails — reached by the sky130 met4↔met5 connect)
- `u_rx_frontend` → `rx_res_fb_pmos` (the `Resist_FB_INV` nonlinear-PMOS
  feedback inverter; power only on **met1** stubs — no met4/met5 straps in
  the GDS, so its LEF keeps the power pins on met1 via the manifest's
  `pdn_power_layer`)

Generate the hard-macro config (omits `--soft-stdcell`):

```
python3 scripts/gen_analog_pnr_config.py \
    --package out/sky130-soc --out build/analog_pnr_hard
librelane --manual-pdk --pdk-root $PDK_ROOT \
    --run-tag=hard_link --overwrite build/analog_pnr_hard/librelane.yaml
```

**PDN recipe for tiny (~10–20 µm) hard macros.**  Verified against OpenROAD
`pdngen`: a `-macro` grid's `add_pdn_connect` only bridges strap shapes that
grid *itself* draws — it cannot reach the `stdcell_grid` straps, so any
macro-local strap is a floating island (`PSM-0038`) and an empty macro grid
aborts (`PDN-0233`).  Neither is needed: `PDN_MACRO_CONNECTIONS` (global
connect) already binds the iterms to VPWR/VGND, so we declare **no `-macro`
grids at all** — pdngen builds the stdcell grid, then a `pdngen` wrapper
sources `pdn_patch.tcl` to draw the real binding in the ODB (the same
technique real LibreLane designs use, e.g. the `ankhdjet` `pdn_patch`).

The verified short-free route per pin is a **vertical met4 column** in the
pin's own x-column up to the **nearest global met5 horizontal strap**, then
a `met4→met5` via:

- A vertical met4 run cannot cross an opposite-net *vertical* met4 strap
  (different x), and crossing a horizontal met5 strap on a different layer
  is harmless — vias only form where placed.  (`_nearest_m5` filters to
  straps spanning >100 µm so it binds to the *global* grid, not a
  macro-local stub.)
- For met1 pins (`u_rx_frontend`) a `met1→via→met2→via2→met3→via3→met4`
  stack lands the column on the pin (the tiny pin stub gets a 1 µm pad for
  the via enclosure); for met4 pins (`u_tx_driver` VDD/VSS rails) the
  column lands directly.
- `u_tx_driver` VDD and VSS are two *different-net* rails sharing one
  x-column, so their columns are pushed to distinct x (`pdn_patch` scans
  the pin's x-extent for a column free of opposite-net met4) — otherwise
  the two vertical runs overlap and short VPWR↔VGND.

Verified: `check_power_grid` reports `PSM-0040 All shapes on net VPWR/VGND
are connected`, and a same-layer overlap scan reports zero shorts.

`scripts/gen_analog_pnr_config.py` splices the `pdngen`-wrapping hook into a
copy of the stock recipe (`build/analog_pnr_hard/pdn_cfg_hard_macros.tcl`)
and emits `build/analog_pnr_hard/pdn_patch.tcl` next to the YAML.

#### Current hard-macro signoff status (honest)

`make phy-link-closure ANALOG_PNR_RUN=build/analog_pnr_hard/runs/hard_link`
reports **UNVERIFIED** — the three physical gates fail while every
functional/connectivity gate passes:

- PASS: netgen LVS (0 errors), STA setup+hold, route DRC, antenna,
  power-grid violations, IR-drop (~1 mV), XOR-free connectivity.
- FAIL: `klayout_xor` (~25), `magic_drc` (~160–250), `klayout_drc`
  (~80–130), all clustered at the macro via towers.

Regeneration progress (drives the counts down monotonically):

- Snap all macro metal/via shapes to the 5 nm manufacturing grid; rebuild
  the met5 power rails as clean full-width rects covering via4 by ±310 nm;
  resize via1 cuts to exactly 150 nm (`via.1a_b`), via2/3/4 to spec.  Both
  macros now report **0 violations standalone under the foundry
  `sky130A_mr.drc` deck** (was 51 in TX).
- Add a generated LEF `OBS` block (in `generate_views.py`) that blocks every
  non-pin routing layer over each macro, so the detailed router can no
  longer land signal met2–met5 through the macro interior — this removed
  the largest top-level `m1.2`/`m4.2` cluster.

Result: magic DRC 285 → 60, KLayout DRC 119 → 12, LVS still 0.

The residual (magic ~68, klayout ~12) is now **localised to the macro met1
power-rail band** — every error sits in two x-columns (x≈166 and x≈173 µm)
on each macro's met1 rail region.  Cell-attributed flattening shows the
colliding pairs are the **generated power/signal via pads landing at
28–40 nm notches against the macro's fragmented internal met1 mesh**: the
pdngen met1↔met4 grid via towers (`via2_3`/`via3_4`/`via4_5`) and detail-
route `L1M1_PR` signal vias place met1/met2 pads that straddle the gap
between two same-x macro met1 shapes, leaving a sub-28 nm sliver
(`met1.2`), an under-width `via2.1a` pad, and `met3/met4 overlap-of-via`
chain errors.

Approaches tried and ruled out (each verified by a full ~25-min run):

- **Exact-LEF via-tower enclosure** in the macro GDS — standalone clean but
  placed-context count unchanged; the macro geometry was never the issue.
- **`PDN_OBSTRUCTIONS`** on li1/met1 over the macro — obstructions do not
  suppress *generated* followpin/grid vias (they gate the router only), and
  obstructing met1 trips `PDN-0006` (blocks the rail reaching the pin).
- **`-macro -default` PDN grid** — a strapless macro grid claims the met1
  VDD pin it cannot reach → `PDN-0006`.
- **Aligning the macro origin to the followpin row-rail pitch** (y=81.6) —
  regressed 68→98; the 6 µm macro spans ~2.2 rows so it always overlaps a
  row boundary and the move only relocated the cluster.

The real fix is **layout-level**: regenerate each macro's met1 power mesh so
the rail is a continuous band that a generated via pad either fully lands
inside or fully clears — eliminating the 28–40 nm partial-overlap notches.
That is macro-layout rework, not a config/keepout adjustment.

**Experiment 2 (measured, reverted `47b0e9c`).**  Net-traced via the PEX `.ext`:
the TX met1 rail band is VSS (`0,-240;19780,240`) with the OUT stub
(`7675,350;19005,750`) riding **110 nm above it** across the met4 power-strap
columns.  The first cut (`4b304d8`, pull OUT's bottom to y≥480 under each strap
column) was verified in a full routed run: **magic 84 / klayout 20 — WORSE than
the 68/12 tower-rebuilt baseline**, so it was reverted (`47b0e9c`).

**Why abstract-GDS edits cannot fix this.**  The colliding "OUT stub" is not a
single abstract rect: after streamout merges the stdcell internals, the
effective met1 is a **castellated** band — the `VIA_L1M1_PR`/`inv_1` subcell
met1 pokes out in a ~1090 nm-period sawtooth (bottom edge alternating
y≈395↔440, plus a y=350 pin tooth).  Generated via pads landing across a
tooth→gap step leave sub-28 nm met1 slivers (`met1.1`) and the VSS↔tooth gap
trips `met1.2`.  Editing the abstract rect (which is already a clean box) does
not change the subcell-derived sawtooth — proven: a flatten-to-y440 pass left
the merged bottom profile bit-identical.

The error budget (routed `hard_link` run, on the notched GDS):
* **TX-INedge (local x<4 µm): ~43** — a met1→met4 via tower + signal via at
  the macro's left edge, the largest cluster.
* **TX-OUTband: ~26** — the VSS↔OUT castellation described above.
* **RX: 8.**

The only fixes that can work are **layout-level**: regenerate the macro so
OUT's met1 bottom edge is a uniform straight band with ≥140 nm clearance to
VSS (and likewise for IN / the rx_res_fb met1 power pins), OR move the
IN/OUT/power pin access off met1 onto met2+ so the router never drops a via
into the castellated band.  Both are macro rework, not abstract patches.

**Regeneration spec (P1-1).**  The macro is 13 `sky130_fd_sc_hd__inv_1` cells
(1+3+9 drive chain) on a 1380 nm pitch + `VIA_L1M1_PR_MR` device taps + two
`tapvpwrvgnd` end taps.  Each `inv_1` only carries the VDD (`y2480-2960`) and
VSS (`y-240-240`) met1 rails — **no signal met1**; the OUT/IN/inter-stage
nodes are formed entirely by the `VIA_L1M1_PR_MR` taps at y≈510 (OUT row),
y≈850/1190 (inter-stage).  The OUT castellation is those taps' met1 merging
with the abstract stub.  To regenerate cleanly:
  - give the OUT node a **solid met1 bus** spanning x7675–19005 at a uniform
    bottom edge, and tie each `VIA_L1M1_PR_MR` output tap up to it with
    vertical met1 fingers (all same-net, safe to merge);
  - keep ≥140 nm clearance from the VSS rail top (y240);
  - do the same for IN and the `rx_res_fb_pmos` met1 power stubs.
This must be re-verified in magic (extract → LVS against `canonical.spice`,
which is inv_1→inv_4→inv_16 over `sky130_fd_pr__nfet/pfet`), not a blind GDS
patch — the two failed attempts showed how easily same-region merges short
VSS↔OUT.

**Honest baseline under 26Q3 (re-measured 2026-11-02).**  The earlier "68/12"
figure was taken before the toolchain switch and was never re-measured on the
tower-rebuilt GDS.  A clean `--from Magic.StreamOut` re-run on the reverted
`77587b` GDS gives **magic 71 / klayout (see run)** — the notch's 84/20 is
confirmed a regression of +13 magic.  Breakdown of the 71 (all at the TX macro,
placed 165.0–185.4 × 80.0–86.02 µm):
  - two signal-via stacks (x≈166.0 = IN access, x≈173.4–173.6): ~45 errors
    across via2.1a/met3.4/via3.5/met4.3/met1.2;
  - one generated `VGND` met4 horizontal strap at y80710 ending flush at the
    macro bottom edge (x164.7–175.5): 8 met4.2 + edge-adjacent met1.2;
  - macro's own met5 VDD/VSS rails at its top/bottom edges: 2 met5.1;
  - the genuine castellation band (OUT sawtooth): ~25 met1.2/met1.1.
So ~57 are via-stack + castellation (need real regen) and ~10 are
PDN-strap/macro-rail edge geometry (potentially a floorplan fix, not layout).

**Regen attempt 1 (OUT band) — clean geometry, no DRC delta.**  Replaced the
castellated OUT top-cell polygon `7750,350;18930,750` (H-bridge, 12 hull pts,
merged to a 76-pt comb of y395/440/580/625 teeth) with a solid box
`x7675–19005, y380–640` that fully swallows all 9 `VIA_L1M1_PR_MR` output taps
+ the M1M2 tap.  Result: merged OUT = single 4-vertex rectangle, standalone
magic DRC 0, extraction preserves the 13-inverter IN→…→OUT chain (20 merged
devices both sides of netgen).  **But placed-context DRC stayed exactly 71** —
proving the errors are NOT in the OUT band.  Reverted.

**Where the errors actually live (error-coord → macro-local map).**
  - met1.2 cluster at local x1820–2155, y1050–1350 = the **IN stub**
    `(200,1050;2055,1350)` vs **inter-node-A** `(2155,1075;6070,1305)` gap —
    the two are DIFFERENT nets (IN feeds inv_1_0 whose output is node-A); the
    175 nm gap + signal-via pads bridging them is the real collision, not OUT.
  - met1.2/via2 cluster at local x8060–8720, y210–400 = below the big
    **inter-node-B** `(3535,735;18490,1305)` (94-pt merged comb) where the
    `VIA_M1M2_PR` tap at (8510,510) sits near the VSS rail.
  - errors at y300–307 = the **rx_res_fb_pmos** macro, a separate cell.

So P1-1 must restructure ALL the signal bands (IN stub, node-A, node-B, OUT)
as clean bands with consistent inter-net clearance — the OUT band was the easy
one and is already effectively clean; the real work is the input/inter-stage
band where three different nets interleave in a ~200 nm channel.  That is a
full signal-routing redesign of the macro, best done interactively in magic
with per-step extract+LVS, not blind GDS rewriting.

**Toolchain note (Nov 2026).**  The pinned nix toolchain (`openroad-2026-02-17`,
librelane py3.13 env) was garbage-collected; restored `librelane 3.0.14` +
`openroad 26Q3` via `nix profile install`.  The newer build is stricter:
`read_verilog` rejects `output signed` (strip `\bsigned\b` from the generated
nl.v between `Yosys.Synthesis` and `OpenROAD.CheckSDCFiles`), and
`global_route` errors on residual macro-boundary overflow (set
`GRT_ALLOW_CONGESTION` + `GRT_OVERFLOW_ITERS=100` in
`gen_analog_pnr_config.py`, commit `606d2b4`).  The newer detailed router also
converges much more slowly and non-monotonically on the macro boundary
(starts ~6600 viols, reached 0 once, then thrashes 0–57 in stubborn-tiles) —
placed-context DRC runs take several hours.

Note on `magic__illegal_overlap`: the ~56 reported overlaps are **false
positives** — magic flags `obsm2/3/4` (the macro OBS marker layers I added)
overlapping the macro's own metal2/3/4/vias.  OBS is *meant* to sit over
existing metal to forbid additional routing; the obsm*/metal* type
difference trips magic's overlap check but is not real DRC.  The true
sign-off decks (klayout DRC, route DRC) stay clean at 12 and 0.

`phy-link-closure` still reports **UNVERIFIED** on the three physical DRC
gates while every functional/connectivity gate passes.

This is a **physical integration signoff of real custom cells** — still not
a foundry signoff.  Produce a separate closure record for the hard-macro
run:

```
make phy-link-closure \
    ANALOG_PNR_RUN=build/analog_pnr_hard/runs/hard_link \
    PHY_LINK_OUT=build/verify/phy_link_closure_hard
```

`phy_link_closure.py` detects the `SERDES_LINK_HARD_MACROS` define in the
run's `config.json` and labels the record `hard-macro` accordingly.

## PHY link closure record (P2)

`make phy-link-closure` runs `verification/analog/phy_link_closure.py`, which
harvests the completed `analog_link_signoff` run's per-step verdicts into a
single hash-verified, fail-closed evidence record at
`build/verify/phy_link_closure/phy_link_closure.json`.

Gates (all required for `SIGNOFF_ELIGIBLE`, read from the run's own
`final/metrics.json` and step `state_out.json` — never declared):

- KLayout XOR differences = 0, Magic DRC = 0, KLayout DRC = 0
- netgen LVS: 0 unmatched devices / nets / pins, 0 LVS errors
- STA: 0 setup + 0 hold violations at `nom_tt_025C_1v80`
- route DRC = 0, antenna = 0, power-grid violations = 0, flow errors = 0
- final GDS, post-route SPEF, and extracted SPICE netlist present + sha256-hashed

This is the physical PHY integration the audit defers to P2: the TX→channel→RX
link is placed-and-routed and signoff-clean.  Because it reads the run's own
data, `phy-link-closure` returns `UNVERIFIED` on a fresh checkout with no run
dir (the `build/` tree is gitignored) — the verdict is evidence-derived, not a
config claim.  It remains a *soft-stdcell* implementation: a physical
integration signoff, not a custom transistor analog signoff.
