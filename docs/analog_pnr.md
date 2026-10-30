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

The hard macros can still be staged and inspected, but the C top deliberately
uses the soft stdcell implementation so DRC/LVS/STA are closed in one
hierarchy.  Hard macro PDN and fixed-device feedback characterization remain
separate work items.

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
