# gf180mcuD device-level closure (nfet_03v3 / pfet_03v3)

## Scope

This note records a **bounded device-level closure lane** for the installed
GlobalFoundries `gf180mcuD` PDK.  It covers one bulk-tied 3.3 V NMOS and one
bulk-tied 3.3 V PMOS at a single size:

```text
PCell     gf180mcu_klayoutapi / nfet, pfet
volt      3.3V
bulk      Bulk Tie
W         1.0 um
L         0.28 um
ld        0.44 um
nf        1
grw       0.36 um
```

The flow builds the PDK PCell, adds **probe-only** gate access and MET1 labels,
then runs Magic DRC, Magic extraction/PEX, KLayout LVS, netgen LVS and an
ngspice DC operating point with the installed models.  It is evidence for this
one device and bias point.  It is **not** a foundry signoff, not a full-cell
qualification, and not a claim of arbitrary W/L, guard-ring, multi-finger or
analog-LUT coverage.

No sky130, M3, Makefile, `pya_pcell*.py` or IHP files were modified by this
lane.  The only technology config changed is
`verification/analog/tech/gf180mcu.yaml`.

## Environment

```text
GF_ROOT  /home/yanlu/.ciel/ciel/gf180mcu/versions/792ef67037d74c8c51811eab3ce23cf793491296
GF_PDK   $GF_ROOT/gf180mcuD
KLayout  0.30.0
Magic    8.3 revision 685
Netgen   1.5.325
ngspice  44.2
```

Magic is invoked with `PDK_ROOT=$GF_ROOT` and the PDK rcfile
`$GF_PDK/libs.tech/magic/gf180mcuD.magicrc`.  KLayout PCell generation uses
`KLAYOUT_PATH=$GF_PDK/libs.tech/klayout` and `klayout -zz -r ...` (batch mode
does not load the implicit KLayout-API macros in this install).

## Reproduce

```sh
python3 verification/analog/gf180_device_verify.py \
  --out build/verify/gf180_device/gf180_device_evidence.json
```

The script writes the GDS, schematics, tool logs, LVS reports and the combined
JSON evidence under `build/verify/gf180_device/`.  It returns 0 only when every
required check passes for every requested device.

## Layout construction

The gf180mcuD PCell generates the MOS diffusion, Poly2 gate, contacts and
Metal1 source/drain/bulk tie, but it generates **no gate contact, no gate pad
and no labels**.  For probing only, the closure adds:

```text
Poly2 extension   400..760 x 1220..1655 nm (GDS 30/0)
Gate contact      470..690 x 1285..1505 nm (GDS 33/0, 220 x 220 nm)
Metal1 gate pad   400..760 x 1250..1655 nm (GDS 34/0)
Labels            S/D/B/G text on GDS 34/10 (MET1TXT)
```

The geometry is derived from the generated GDS (Poly2/COMP/Metal1 boxes), not
hard-coded to the nfet example.  For these two PCells at these parameters the
Poly2, COMP and Metal1 boxes happen to be identical; the script still computes
the gate pad from the actual boxes so a future PCell change cannot silently
reuse nfet coordinates.

The gate contact is 220 nm (the PDK contact size), is at least 165 nm from the
active edge, has at least 65 nm Poly2 overlap, and the Metal1 gate pad is at
least 0.1444 um^2 and at least 230 nm from the source/drain Metal1.  These were
the DRC constraints that the first, smaller probe contact/pad violated.

## Results

| Device | Magic DRC | KLayout LVS | netgen LVS | PEX R/C/X | Model DC op |
|---|---:|---|---|---:|---|
| `nfet_03v3` | 0 errors | `Netlists match.` | `Circuits match uniquely.` | 0 / 6 / 1 | pass |
| `pfet_03v3` | 0 errors | `Netlists match.` | `Circuits match uniquely.` | 0 / 10 / 1 | pass |

Both devices extract exactly four ports (`D`, `S`, `G`, `B`) and one device
whose extracted model name is the PDK subckt name (`nfet_03v3` /
`pfet_03v3`).  The model binding is present in
`$GF_PDK/libs.tech/ngspice/sm141064.spice` and is exercised by ngspice.

### nfet_03v3

```text
DRC          Total DRC errors found: 0
LVS ports    D S G B
PEX          X=1, R=0, C=6
DC bias      VGS=1.65 V, VDS=3.3 V, VSB=0 V
DC result    V(D)=3.3 V, V(G)=1.65 V, V(S)=0 V, V(B)=0 V
             I(VDD) = -1.66791e-04 A  (drain current 166.8 uA)
```

### pfet_03v3

```text
DRC          Total DRC errors found: 0
LVS ports    D S G B
PEX          X=1, R=0, C=10
DC bias      VSG=1.65 V, VSD=3.2 V, VSB=0 V
DC result    V(D)=0.1 V, V(G)=1.65 V, V(S)=3.3 V, V(B)=3.3 V
             I(VDD) = -5.68711e-05 A (source current 56.87 uA)
             I(VDS) =  5.68748e-05 A (drain current 56.87 uA)
```

The PMOS bulk tie is **not blocked** at this device/bias point: the extracted
layout retains a distinct `B` port, the bulk tie is connected, KLayout and
netgen both match, and the PEX netlist carries the `B` node.  This result does
not extend to guard-ring, deep-nwell, array or multi-finger variants.

## LVS tool note

KLayout performs the layout-vs-schematic extraction and comparison against the
primitive-M schematic.  netgen then compares that KLayout target netlist with
the same schematic as an independent pin/device-class check.  Magic's
`ext2spice` output is used for PEX and the model simulation, not as the netgen
layout input: Magic writes the device as `X0 ... nfet_03v3`, which netgen
interprets as a hierarchical subcircuit rather than a MOS primitive.  This is a
tool-interface limitation, not a hidden pass.

## Negative control

The script copies the good GDS, removes exactly one `B` label and reruns the
LVS flow.  netgen then reports:

```text
Final result: Top level cell failed pin matching.
```

for both devices.  For the nfet it shows `SUB` versus `B **Mismatch**`; for the
pfet the extracted netlist loses the body pin and netgen reports no devices.
This confirms the body pin/label is structurally required by the pin-sensitive
comparison.

The current gf180mcuD PDK `gf180mcu.lvs` `check_ports=true` path is not usable:
it raises `undefined local variable or method 'flag_missing_ports'`.  With
`check_ports=false` KLayout LVS is pin-insensitive, so the no-`B` negative
control still matches in KLayout; the failure is demonstrated with netgen.  This
limitation is recorded in the evidence rather than hidden.

## PEX note

Magic extraction runs:

```text
extract
extresist all
ext2spice cthresh 0
ext2spice extresist on
ext2spice -o <raw>
```

`ext2spice cthresh 0` is required to emit the small device capacitances; without
it the raw netlist contains only the device and the `.ext` capacitance data is
not written to SPICE.  For this single-Metal1 device `extresist all` produces no
resistive elements, so `R=0` in the PEX counts.  This is a real result for this
geometry, not a claim that the device has no parasitic resistance.

The pfet PEX netlist also contains parasitic capacitors to the p-substrate
node `VSUBS`.  The DC testbench exposes `VSUBS` as a fifth PEX subcircuit port
and ties it to ground; leaving it floating inside the subckt produces a
singular matrix warning in ngspice.  The nfet PEX has no `VSUBS` elements.

## Blockers and non-claims

- **Not a foundry signoff.**  The flow uses the installed PDK decks and models
  but is not a GF-qualified signoff.
- **One device per type, one size, one bias point.**  No W/L sweep, no
  multi-finger, no guard ring, no deep nwell, no array/well-proximity effects.
- **Probe-only geometry.**  The gate contact, gate pad and MET1 labels are added
  for verification; they are not part of the PCell and are not a layout
  template for production.
- **KLayout LVS pin checking is broken in this PDK release.**  The flow uses
  `check_ports=false` and relies on netgen for pin-sensitive comparison.
- **R=0 in PEX** for this short single-Metal1 device; no RC-network fidelity
  claim is made.
- **Generic schematic LUT flow remains scaffold.**  The YAML marks
  `analog_lut_status: scaffold`; `make analog-lut ANALOG_TECH=gf180mcu` is not
  verified by this work.

## Evidence

```text
build/verify/gf180_device/gf180_device_evidence.json
build/verify/gf180_device/nfet/
build/verify/gf180_device/pfet/
```

The JSON records exact command arrays, working directories, return codes, DRC
counts, extracted ports/model names, PEX element counts, LVS results, model
simulation values and the negative-control result.
