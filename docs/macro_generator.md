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
python3 verification/analog/macro_gen.py --list-tech
python3 verification/analog/macro_gen.py --tech gf180mcu \
  --topology tx_inv_chain --out generated/gf180mcu/tx_inv_chain
```

| tech | device style | devices | macro defaults (W/L, fb W/L) | macro generation |
|---|---|---|---|---|
| sky130 | subckt | `sky130_fd_pr__nfet/pfet_01v8` | 5/0.15, 0.55/8 | schematic |
| gf180mcu | subckt | `nfet_03v3` / `pfet_03v3` | 5/0.28, 0.55/8 | schematic |
| ihp_sg13g2 | subckt | `sg13_lv_nmos` / `sg13_lv_pmos` (`ng`) | 5/0.13, 0.55/2 | schematic |
| icsprout55 | subckt | `nm1p2_svt_lp` / `pm1p2_svt_lp` (`nf`) | 2/0.06, 0.55/1 | schematic |
| etri050 | `mos` model cards | `nmos4` / `pmos4` | 5/0.6, 1/5 | schematic |
| freepdk45 | — | `NMOS_VTG` / `PMOS_VTG` not verified | — | **fail closed** |

For icsprout55 the PDK's repaired KLayout DRC/LVS decks are usable at cell
level (`make ics55-drc-lvs`); generated macros remain `SCHEMATIC_ONLY` with
`circuit.spice`, `macro.v` and a manifest, and layout/LEF/Liberty/PEX stay
`missing` unless an explicit
`--layout-gds`/`--lef`/`--cdl`/`--pex` with clean DRC/LVS is attached.  The
verified gf180mcu/IHP `rx_fb_pcell` macros are a different, three-inverter
topology built by their bespoke builders, not by this generator; the generic
RX topology here is the single inverter plus series PMOS feedback
(`rx_res_fb_inv`).  freepdk45 has no verified discrete device binding (its
ngspice library exposes Nangate cells), so it stays fail-closed.

W/L defaults come from `verification/analog/tech/<tech>.yaml`
`macro_defaults`; override them with `--w-finger-um`, `--l-um`, `--fb-w-um`,
`--fb-l-um`, `--n-parallel`, `--p-parallel` and `--stages`.

A model-level DC check of the generated inverter chains (VIN=0) was run for
gf180mcu (3.3 V), ihp_sg13g2 (1.2 V, OSDI PSP103; the optional cap OSDI libs
are absent), icsprout55 (1.2 V) and etri050 (5.0 V): all converge with
`VOUT=VDD`.  This is a connectivity/model-load check, not DRC/LVS/PEX or a
speed claim.

The TX generator preserves the 1/3/9 NMOS and 2/6/18 PMOS stage ratios.
The RX generator preserves the three-parallel NMOS / six-parallel PMOS
inverter and two series diode-connected long-channel PMOS feedback devices.

## Physical layout contract

A physical template must contain real diff/poly/li1 device layers, not only
met1 pin rectangles.  For the current repository GDS this audit fails:

```text
abstract fixture only: missing diff, poly, li1
```

That is intentional.  Supplying a physical GDS only reaches
`LAYOUT_ATTACHED`; it is not topology proof.  To reach `SIGNOFF_ELIGIBLE` the
manifest must also carry a clean DRC report, a unique-match LVS report for the
same generated circuit, and a PEX view.  A supplied LVS report that does not
prove a unique match is rejected outright.

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
is rejected.  Optional `--lef`, `--cdl`, `--pex`, `--drc-report`, and
`--lvs-report` attach explicit PCell views/evidence; the layout top cell must
equal `--name`, otherwise generation fails closed.  GDS-only manifests carry
`status=LAYOUT_ATTACHED` and `physical_signoff_eligible=false`; only the
report-backed combination can become `SIGNOFF_ELIGIBLE`.  File hashes in
manifests bind GDS, LEF, SPICE, Liberty and PEX.

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

- supported: `nfet/pfet 01v8`, arbitrary W/L, `nf==1..N`,
  `bulk=None/bulk tie`, gate contact top/bottom;
- unsupported: guard-ring boolean operations, interdigitated patterns,
  RF devices; these fail rather than silently approximating.
- `bulk tie` adds an n/p tap and met1 body strap; headless device LVS
  (`pya_pcell_verify.py --bulk 'bulk tie'`) matches pfet and nfet with an
  explicit `B` terminal.  The sky130 RX macro now uses bulk-tied devices with
  macro-level VDD/VSS body straps and a met2 `net06` bridge; see
  `docs/klayout_pcell.md`.

The port uses `verification/analog/pya_draw/` and a small pya-backed
`gdsfactory` compatibility shim.  A headless probe can be run with:

```bash
GDS=<sky130_fd_pr.gds> OUT=/tmp/probe.gds \
  klayout -b -r verification/analog/klayout_pcell_probe.py
```

The separate `external_magic` path remains available as a second backend
(`magic_pex.py` / `Magic gencell`) for devices whose geometry is not covered by
the pure-pya subset.
