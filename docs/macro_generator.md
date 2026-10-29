# Cross-process analog macro generator

`verification/analog/macro_gen.py` generates the portable circuit/contract
layers:

```text
topology (inverter chain / feedback inverter)
  -> tech YAML device binding
  -> circuit.spice + macro.v + manifest.json
  -> optional explicit PDK layout template
```

It deliberately does **not** invent layout, LEF or Liberty.  Physical views are
`missing` unless an explicit `--layout-gds` template is supplied.  Supplied
layouts are audited for transistor layers (`diff`, `poly`, `li1`); an
abstract/pin-only GDS is rejected instead of being advertised as signoff.

## Supported now

```bash
python3 verification/analog/macro_gen.py \
  --tech sky130 --topology tx_inv_chain \
  --stages 1,3,9 --out generated/sky130/tx_inv_chain
```

- sky130: supported schematic + black-box Verilog generation
- gf180mcu / IHP SG13G2: `status: scaffold`; generation fails closed until
  device model names, W/L ranges and layout templates are verified.

The TX generator preserves the 1/3/9 NMOS and 2/6/18 PMOS stage ratios.
The RX generator preserves the three-parallel NMOS / six-parallel PMOS
inverter and two series diode-connected long-channel PMOS feedback devices.

## Physical layout contract

A physical template must contain real diff/poly/li1 device layers, not only
met1 pin rectangles.  For the current repository GDS this audit fails:

```text
abstract fixture only: missing diff, poly, li1
```

That is intentional.  The schematic generator remains useful for device
binding and contract generation, while physical signoff stays fail-closed.

## Portability rules

- Device names, supply, W/L and corner data come from `verification/analog/tech`.
- No absolute PDK-version path is embedded in generated manifests.
- No PDK without a supported device binding and layout template is silently
  substituted with an ideal resistor or a generic inverter.
- The generated `macro.v` is a black box; timing must not be inferred from it.

## Stdcell reuse backend

`verification/analog/stdcell_macro_gen.py` is the reusable stdcell backend:

```bash
make analog-stdcell-gen
```

- TX: `inv_1 -> inv_4 -> inv_16`
- RX: `inv_4`
- Device names come from the tech YAML `stdcell` block.
- It emits a netlist only; layout/LEF/Liberty/PEX are owned by the target
  standard-cell flow.
- Unsupported PDKs fail closed instead of substituting a cell.

The C integration uses this backend as soft macros, so DRC/LVS/STA are closed
in one standard-cell hierarchy.  The hard macro views are generated separately
and are not silently treated as timing-characterized macros.

## Identity and evidence gate

Physical signoff is rejected unless all of the following hold:

```text
electrical_topology == physical_topology
topology_match == true
layout.physical == true
lvs.status in {match-unique, clean, correct}
magic_pex path exists
canonical_spice path exists
```

The RX identities are deliberately separate:

- `rx_parallel_inv_macro`: three parallel stdcell inverters, physical
  GDS/LEF/PEX, no feedback field.
- `rx_res_fb_pmos`: original full GDS with the inverter plus two series
  nonlinear PMOS feedback devices; its canonical SPICE is the matching
  post-layout PEX wrapper.
- The soft `inv_4` RX used by C is a separate soft identity and is not
  advertised as a feedback macro.

`--layout-gds` also requires physical `diff/poly/li1`; a pin-only abstract GDS
is rejected.  Optional `--lef`, `--cdl`, and `--pex` attach explicit PCell
views; the layout top cell must equal `--name`, otherwise generation fails
closed.  File hashes in manifests bind GDS, LEF, SPICE, Liberty and PEX.

Example for the verified PCell RX macro:

```bash
/usr/bin/python3 verification/analog/macro_gen.py --tech sky130 \
  --topology rx_res_fb_inv --name rx_fb_pcell \
  --n-parallel 3 --p-parallel 6 --w-finger-um 5 --l-um 0.15 \
  --fb-w-um 0.55 --fb-l-um 8 \
  --out build/verify/pcell_rx_macro \
  --layout-gds build/verify/pcell_rx_fb/rx_fb_pcell.gds \
  --lef build/verify/pcell_rx_fb/rx_fb_pcell.lef \
  --cdl build/verify/pcell_rx_fb/rx_fb_pcell.cdl \
  --pex build/verify/pcell_rx_fb/rx_fb_pcell.pex.spice
```

## Pure-pya PCell backend

`verification/analog/pya_pcell.py` ports the supported subset of the PDK
`draw_fet.py` to KLayout's native `pya` API:

- supported: `nfet/pfet 01v8`, arbitrary W/L, `nf==1..N`, `bulk=None`,
  gate contact top/bottom;
- unsupported: guard-ring boolean operations, interdigitated patterns,
  RF devices; these fail rather than silently approximating.

The port uses `verification/analog/pya_draw/` and a small pya-backed
`gdsfactory` compatibility shim.  A headless probe can be run with:

```bash
GDS=<sky130_fd_pr.gds> OUT=/tmp/probe.gds \
  klayout -b -r verification/analog/klayout_pcell_probe.py
```

The separate `external_magic` path remains available as a second backend
(`magic_pex.py` / `Magic gencell`) for devices whose geometry is not covered by
the pure-pya subset.
