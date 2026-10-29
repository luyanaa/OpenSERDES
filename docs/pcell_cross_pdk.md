# PCell cross-PDK probe

All four installed PDKs have a usable KLayout-side parameterized device path,
but the maturity is not equal.  The ICsprout55 PDK is tested in addition to
the three `.ciel` PDKs.

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

## ICsprout55

The installed PDK under `/grid/icsprout55-pdk` (also mirrored in the ECOS
workspace) ships pure-pya KLayout PCells:

```text
libs.tech/klayout/pcells/ics55_mos_analog.py
libs.tech/klayout/pcells/ics55_rf_mos.py
library: ICsprout55
pcells: ics55_mos_analog, ics55_rf_mos
```

Generate the probe with:

```sh
ICS55_PDK=/grid/icsprout55-pdk OUT=/tmp/ics55_pcells.gds \
  klayout -b -r verification/analog/pcell_probe_ics55.py
```

`verification/analog/pcell_probe_ics55.py` instantiates both:

```text
ics55_mos_analog n/p W=1.0 L=0.06 fingers=2 guard_ring=1
ics55_rf_mos     n/p wf=2.0 L=0.06 nf=4
```

The RF MOS pair is clean under the PDK's partial KLayout DRC port:

```text
KLayout DRC, tables=main, no_density=true, no_mom=true
rf_n: 0 violations
rf_p: 0 violations
```

The analog MOS is not yet clean for multi-finger geometry:

```text
mos_n fingers=2: 1 x poly.poly.spacing
mos_p fingers=2: 1 x poly.poly.spacing
mos_n/p fingers=1: 0 violations
```

This is a real limitation of the provisional PCell template, not a pass.
The PDK's own DRC port is explicitly **not Calibre-equivalent**; no LVS or
foundry signoff is claimed.

## Portability conclusion

| PDK | PCell source | arbitrary W/L | DRC | repo LVS |
|---|---|---|---|---|
| sky130A | pure pya port | yes | yes | nf=1 yes |
| gf180mcuD | PDK KLayout API | yes | yes | not yet |
| IHP SG13G2 | PDK CNI/pycell4klayout | yes | yes | not yet |
| ICsprout55 | PDK pure-pya PCells | yes | RF yes; analog fingers=1 yes, >1 one poly-spacing | not yet |

The correct generator rule is therefore: PCell availability is a capability
probe, not a promise that a macro is signoff-ready.  Each PDK still needs a
device binding (models, ports, LVS/CDL, DRC deck) and a layout template.

## Supplemental model PDKs

FreePDK45/Nangate45 and ETRI050 are model/standard-cell supplements rather than
additional PCell backends.  Their probes and limitations are recorded in
`docs/supplemental_pdks.md`.
