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
RX forwarded clock is generated from tx_clk (source-synchronous)
```

Latest completed run:
`build/analog_pnr_sky130/runs/analog_link_signoff`

```text
XOR differences: 0
Magic DRC:       0
KLayout DRC:     0
LVS:             0 unmatched devices/nets/pins
Setup WNS:       +12.893 ns
Hold WNS:        +0.047 ns (at 25 Mb/s / 40 ns TX UI)
PDN:             generated successfully
```

The flow still reports non-gating max-slew and max-cap warnings.  They are
recorded but are not claimed as closed signoff limits.

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
