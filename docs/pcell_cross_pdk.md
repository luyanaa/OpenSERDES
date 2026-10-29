# PCell cross-PDK probe

All three installed PDKs have a usable KLayout-side parameterized device path,
but the maturity is not equal.

## sky130A

Backend: `verification/analog/pya_pcell.py` (pure pya port of `draw_fet`)

```text
nfet/pfet 01v8
arbitrary W/L, nf=1 LVS-closed
guard-ring/interdig geometry DRC only
```

Validated:

```text
nfet W=5.00 L=0.15 nf=1  DRC 0, LVS match
pfet W=5.00 L=0.15 nf=1  DRC 0, LVS match
pfet W=0.55 L=8.00 nf=1  DRC 0, LVS match
```

## gf180mcuD

The PDK ships a KLayout-API PCell library:

```text
gf180mcuD/libs.tech/klayout/tech/pymacros/klayout_api_cells
library: gf180mcu_klayoutapi
pcells: nfet, pfet, ...
```

Test:

```text
nfet 3.3 V, W=1.0 um, L=0.28 um
KLayout PCell generation: OK
Magic DRC with gf180mcuD deck: 0 errors
```

This confirms the PDK satisfies the `klayout PCell + arbitrary W/L` assumption
for geometry.  It is not yet wired into the macro generator and has no
per-device LVS/analog binding in this repository.

## IHP SG13G2

The PDK ships a CNI/pycell4klayout PCell library:

```text
ihp-sg13g2/libs.tech/klayout/python/sg13g2_pycell_lib
library: SG13_dev
pcells: nmos, pmos, ...
```

The KLayout `create_cell` convenience path did not materialize a cell in this
environment, but the PCell declaration can be driven directly through
`decl.produce(...)`.  A 1.0/0.13 um nmos generated successfully:

```text
bbox: 0.81 x 1.36 um
KLayout SG13G2 DRC (minimum rules): PASS, 0 violations
```

The PDK DRC runner expects a repo-level `versions.txt`; it was provided
temporarily for the probe.  The PCell is **geometry-verified**, not yet
LVS/analog-bound in this repository.

## Portability conclusion

| PDK | PCell source | arbitrary W/L | DRC | repo LVS |
|---|---|---|---|---|
| sky130A | pure pya port | yes | yes | nf=1 yes |
| gf180mcuD | PDK KLayout API | yes | yes | not yet |
| IHP SG13G2 | PDK CNI/pycell4klayout | yes | yes | not yet |

The correct generator rule is therefore: PCell availability is a capability
probe, not a promise that a macro is signoff-ready.  Each PDK still needs a
device binding (models, ports, LVS/CDL, DRC deck) and a layout template.
