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

The residual DRC is **not** an open short or a broken connect — it is the
macros' dense internal layout.  The KLayout report attributes **~84 of ~118
errors to `tx_inv_chain_1_3_9` itself** (`m5.1` width, `via.1a_b` enclosure,
`m1.2`/`m4`/`via4` OFFGRID, `via4.1_b`), with most of the rest to the
`serdes_analog_link` top cell where the macros' met1/met4 crowd neighbouring
geometry at 10–40 nm.  Each macro GDS is **DRC-clean standalone** (verified:
`drc count total` = 0) because the standalone run has no adjacent metal;
when flattened against the `pdn_patch` landing pads and the powered stdcell
rows, the macros' min-spacing/min-width internal metal violates the deck.

Iterative GDS patching oscillates (each fix trades errors: 285 → 163 → 156
→ 158 across runs) because the crowding is pervasive, not a single site.
The correct fix is a **from-scratch layout regeneration** of
`tx_inv_chain_1_3_9` and `rx_res_fb_pmos` that re-spaces internal metal to
keep ≥ 1× spacing from the cell boundary and uses rule-driven via arrays —
genuine custom-layout work, not coordinate patching.  Connectivity, LVS,
timing, power-grid, route-DRC and antenna are all clean meanwhile.

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
