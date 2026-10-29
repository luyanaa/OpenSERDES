# KLayout PCell feasibility for analog macros

## Finding

KLayout's PCell infrastructure is available in the embedded Python runtime:

- `pya.PCellDeclarationHelper` and `pya.Library` are present.
- The Sky130 PDK ships `cells/__init__.py` which registers `nfet`, `pfet`,
  resistor, capacitor, diode and RF PCells under library `skywater130`.
- A pure-`pya` PCell that copies a verified fixed device from
  `sky130_fd_pr.gds` was instantiated headlessly and produced a physical cell
  (`3.38 x 5.97 um`, 336 shapes).  This proves the PCell template path works
  without a GUI.

## Important limitation

The PDK's parameterized MOS/R/C PCell implementations are not standalone:

```text
cells/fet.py -> draw_fet.py -> gdsfactory/kfactory
```

Default KLayout Python has no `gdsfactory`.  Installing current
`gdsfactory`/`kfactory` into a local target makes the library import, but the
PDK code then hits version-coupled APIs (`_get_default_kcl`, `Cell.add_array`)
and fails during `produce_impl`.  So the PDK PCell code cannot be treated as a
portable generator without either:

1. pinning a mutually compatible `gdsfactory`/`kfactory`/PDK version and
   validating DRC/LVS, or
2. porting `draw_fet.py`/`draw_rf.py` to pure `pya` geometry, or
3. exposing verified fixed-device GDS cells through a PCell selector.

## Recommended template architecture

A portable PCell backend should be split into:

```text
tech YAML
  pcell_library: skywater130
  pcell_name: nfet
  parametrization: fixed_cells | pure_pya | external_magic
  device_cells: [...]
        |
KLayout PCell adapter
  fixed_cells: choose a DRC/LVS-verified cell, fail closed otherwise
  pure_pya: emit diff/poly/li/metal/via geometry from PDK rules
  external_magic: invoke a deterministic device generator (not ideal)
        |
GDS + LEF + CDL + PEX + hash
```

The PCell wrapper must not claim arbitrary W/L support unless the geometry
generator and DRC/LVS checks cover it.  Fixed-cell selection is useful for
discrete variants; the two long-channel feedback PMOS devices need either a
verified fixed cell or a pure-pya/PCell geometry implementation.

## Prototype status

- Pure-pya fixed-device PCell: **works**.
- PDK `fet.py` PCell with latest gdsfactory: **does not run** due API mismatch.
- Parameterized long-channel PMOS PCell: **not yet implemented**.
- Recommended next step: add a pure-pya MOS geometry port with a narrow
  parameter contract (`type`, `w`, `l`, `nf`, `m`, `bulk`) and gate it behind
  independent Magic/KLayout DRC and netgen LVS.

## Ported subset status (2026-10-29)

`verification/analog/pya_pcell.py` now registers a pure-pya `mos` PCell and
uses the ported `verification/analog/pya_draw/` subset.  Headless tests
generated and Magic-DRC-checked:

```text
nfet W=5.00 L=0.15 nf=1   DRC 0
pfet W=5.00 L=0.15 nf=1   DRC 0
pfet W=0.55 L=8.00 nf=1   DRC 0
nfet W=5.00 L=0.15 nf=3   DRC 0
pfet W=5.00 L=0.15 nf=3   DRC 0
```

The subset is intentionally narrow:

- `bulk=None`, `gate_con_pos=top/bottom`;
- no guard-ring boolean operations or interdigitated gate patterns;
- RF/high-voltage devices are not claimed.

`external_magic` remains the second backend via the existing Magic gencell/PEX
flow.  Device-level LVS labels/ports for the pure-pya PCell still need to be
added before using it as a complete macro generator backend.
