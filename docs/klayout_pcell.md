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

The subset currently supports:

- `bulk=None`, `bulk tie`, and `bulk=guard ring`;
- `interdig=0/1` with a supplied pattern;
- `gate_con_pos=top/bottom`;
- RF/high-voltage devices are not claimed.

`external_magic` remains the second backend via the existing Magic gencell/PEX
flow.  Device-level LVS labels/ports for the pure-pya PCell still need to be
added before using it as a complete macro generator backend.

### Terminal labels

The pure-pya PCell now adds `D/G/S` labels on the generated met1 terminals.
Magic extraction produces one transistor for the `W=5/L=0.15 nf=1` case:

```text
X0 D G S VSUBS sky130_fd_pr__nfet_01v8 w=5 l=0.15
```

The top-level cell still needs explicit Magic port promotion before a strict
top-level LVS pin match; the geometry/DRC portion is complete, device-level
LVS is not yet advertised as closed.

## Device-level LVS probe

`verification/analog/pya_pcell_verify.py` promotes D/G/S labels to a parent
cell, runs Magic extraction, and compares the extracted one-finger device to
the PDK subcircuit-style schematic with netgen.  Guard-ring and interdigitated
geometry variants are DRC-checked; their LVS is not yet part of the one-finger
contract.

Verified unique match:

```text
nfet W=5.00 L=0.15 nf=1   LVS match uniquely
pfet W=5.00 L=0.15 nf=1   LVS match uniquely
pfet W=0.55 L=8.00 nf=1   LVS match uniquely
```

`nf=3` geometry is DRC-clean but its multi-finger extracted topology does not
yet match the scalar parallel schematic; it remains geometry-only until the
gate/source/drain finger connection pattern is corrected.  Arbitrary W/L with
`nf=1` is the current LVS-closed PCell contract.

## Unit inverter from PCell devices

`verification/analog/pya_inverter_probe.py` builds a two-device inverter from
the pure-pya PCell devices (N and P, W=5/L=0.15) and routes:

- gates via a met2 bridge;
- drains via met2/met3 to OUT;
- sources to VSS/VDD rails;
- IN/OUT/VDD/VSS labels.

Headless result:

```text
Magic DRC: 0
netgen LVS: Circuits match uniquely
```

Extracted topology:

```text
Xinv_pfet_0 IN OUT VDD inv_pfet
Xinv_nfet_0 IN OUT VSS VSUBS inv_nfet
```

This is the unit primitive.

## TX 1/3/9 PCell array

`verification/analog/pya_tx_array.py` places 13 unit inverters as
`1 -> 3 -> 9` stages.  Upper-metal interconnect uses met3/met4 distribution
and met1 rails; the top cell is `tx_pcell`.

Reproducible headless evidence:

```sh
make analog-pcell-tx-verify
```

Result:

```text
Magic DRC: 0
netgen LVS: Circuits match uniquely
bbox: 56.725 x 8.480 um (database units -125,-380;56600,8100)
LEF: build/verify/pcell_tx/tx_pcell.lef
CDL: build/verify/pcell_tx/tx_pcell.cdl
```

The default 1/3/9 hierarchy is:

```text
stage 1: 1 inverter
stage 2: 3 parallel inverters
stage 3: 9 parallel inverters
```

The C-compatible stage ratio is also generated and verified:

```sh
make analog-pcell-tx-1-4-16-verify
```

```text
stage 1: 1 inverter
stage 2: 4 parallel inverters
stage 3: 16 parallel inverters
Magic DRC: 0
netgen LVS: Circuits match uniquely
```

This is a PCell array topology, not a transistor-level copy of the C std-cell
`inv_1 -> inv_4 -> inv_16` physical macro.  LEF/CDL views are generated and
the CDL is LVS-matched to the extracted layout; PEX and the RX feedback array
remain open.  No signoff claim is made.

## RX nonlinear-feedback PCell

`verification/analog/pya_rx_feedback.py` builds the PCell RX macro:

```text
3 unit inverters              (3 N / 3 P, W=5/L=0.15)
+ 3 parallel PMOS devices     (3 P, W=5/L=0.15)
= 3 N / 6 P inverting core
+ 2 series diode-connected PMOS feedback devices (W=0.55/L=8)
```

Reproducible headless evidence:

```sh
make analog-pcell-rx-fb-verify
```

Result:

```text
Magic DRC: 0
netgen LVS: Circuits match uniquely
bbox: 23.860 x 10.650 um (database units -305,-380;23555,10270)
LEF: build/verify/pcell_rx_fb/rx_fb_pcell.lef
CDL: build/verify/pcell_rx_fb/rx_fb_pcell.cdl
```

The extracted hierarchy has three `rx_inv` instances, three extra
`rx_pfet_w5` instances, and two `rx_pfet_fb` instances.  The feedback path is
the intended two-device series PMOS diode chain between `OUT` and `IN`.

The pure-pya device cells support `bulk tie`; headless Magic/netgen probes
match both `pfet W=5/L=0.15` and `nfet W=5/L=0.15` with explicit G/D/S/B
ports; Magic DRC is 0 for both.  The negative control `--drop-body-label`
intentionally removes the B label and fails LVS.

The RX feedback macro is now **macro-level body-tie closed**:
`rx_fb_pcell` uses bulk-tied devices, straps NMOS bodies to VSS and PMOS bodies
to VDD, separates the two L=8 feedback devices, and routes the `net06`
feedback bridge on met2 so it cannot short the first feedback body tap.  The
result is Magic DRC 0, hierarchical netgen LVS match (8 instances, 5 nets), and
Magic RC PEX with 11 devices / 0 R / 6 C.  A negative build with the body
straps removed fails LVS.  Evidence: `build/verify/pcell_rx_fb.json` and
`build/verify/pcell_rx_fb/`.  This is still diagnostic PCell evidence, not STA
or production signoff.
