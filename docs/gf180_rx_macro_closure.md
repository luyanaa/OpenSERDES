# gf180mcuD RX macro closure (3 inverters + 3 extra PMOS + 2 feedback PMOS)

## Scope

This note records a bounded **macro-level** closure for a gf180mcuD RX
nonlinear-feedback inverter matching the electrical intent of the sky130
`rx_fb_pcell`:

```text
3 x CMOS inverter        nfet_03v3 W=5u L=0.28u
                         pfet_03v3 W=5u L=0.28u
3 x extra PMOS           pfet_03v3 W=5u L=0.28u
                         source VDD, drain OUT, gate IN, bulk VDD
2 x series feedback PMOS pfet_03v3 W=0.55u L=8u
                         OUT -> fb1(source/gate OUT, drain net06)
                         -> fb0(source/gate net06, drain IN)
                         bulk VDD
ports                    VSS VDD OUT IN
```

The result is **PASS** for this placement and size set.  It is not a full-chip
or foundry signoff, not an arbitrary-W/L claim, and not a full RC parasitic
signoff (`R=0` in this single-M1-rail macro; see PEX note).

No sky130, M3, Makefile, IHP or `pya_pcell*` files were modified by this lane.
The only technology config changed is
`verification/analog/tech/gf180mcu.yaml`.

## Environment

```text
GF_ROOT  /home/yanlu/.ciel/ciel/gf180mcu/versions/792ef67037d74c8c51811eab3ce23cf793491296
GF_PDK   $GF_ROOT/gf180mcuD
KLayout  0.30.0
Magic    8.3 revision 685
Netgen   1.5.325
```

Magic uses `PDK_ROOT=$GF_ROOT` and
`$GF_PDK/libs.tech/magic/gf180mcuD.magicrc`.  KLayout PCell generation uses
`KLAYOUT_PATH=$GF_PDK/libs.tech/klayout` and `klayout -zz -r ...`.

## Reproduce

```sh
python3 verification/analog/gf180_rx_macro.py \
  --out build/verify/gf180_rx_macro/gf180_rx_macro_evidence.json
```

The script writes the GDS, flat primitive-M schematic, hierarchical CDL,
DRC/LVS/PEX logs/reports and the combined JSON evidence under
`build/verify/gf180_rx_macro/`.

## Layout construction

The PDK `gf180mcu_klayoutapi` PCell supplies each MOS body/bulk tie.  The
builder adds the same probe-only gate access used by the device closure
(220/260 nm contact on Poly2 above active, Poly2/M1 gate pad) and then routes:

```text
VSS rail      Metal1 at y = -0.8 .. -0.5 um
VDD rail      Metal1 at y = 25.5 .. 25.8 um
OUT bus       Metal2 at y = 8.0 .. 8.5 um
IN bus        Metal2 at y = 10.0 .. 10.5 um
net06         Metal2 local bus above the feedback devices
```

Floorplan:

- 3 NMOS devices in a bottom row (`x = 0, 8, 16 um`), normal orientation.
- 6 PMOS devices in a middle row (`x = 0, 8, 16, 24, 32, 40 um`), mirrored
  vertically so their gate pads face the IN/OUT routing channel and their
  source/bulk M1 bars can strap straight up to the VDD rail.
- 2 long-L feedback PMOS devices in a top row (`x = 0, 12 um`), normal
  orientation.

All NMOS bulk M1 taps are strapped to VSS and all PMOS bulk M1 taps to VDD.
The extracted netlist confirms the body pins are VSS/VDD; there is no
`Bulk=None` device.

The feedback sizes are the requested `W=0.55u / L=8u`.  The PCell accepted
`L=8u` and Magic DRC is clean, so no feedback-size deviation was needed.

## Results

| Check | Result |
|---|---|
| Magic DRC | **0 errors** |
| KLayout LVS (flat M reference) | **Netlists match.** |
| netgen LVS (flat M reference) | **Circuits match uniquely.** |
| netgen LVS (8-block hierarchical CDL) | **Circuits match uniquely.** |
| LVS device instances | 11 (3 `nfet_03v3`, 8 `pfet_03v3`) |
| LVS nets | 5 (`VSS`, `VDD`, `OUT`, `IN`, internal `net06`) |
| Magic PEX | 11 devices, R=0, C=10, X=11 |
| PEX body nodes | `nfet_03v3` -> VSS, `pfet_03v3` -> VDD |
| Negative control | PMOS bulk strap removed -> LVS fails |

The block count is **8** (3 inverter blocks + 3 extra PMOS + 2 feedback
PMOS); the flat MOS device count is **11**.

## LVS reference note

The KLayout LVS run uses a **flat primitive-M schematic** with 11 `M` devices.
This is required by the PDK LVS reader: it does not flatten `X` inverter
subcircuits in the reference (a hierarchical reference with 8 `X` instances is
reduced to the 6 inverter devices and fails).  The deliverable therefore
includes both:

- `gf180_rx_macro.spice` - flat primitive-M schematic used for LVS;
- `gf180_rx_macro.cdl` - hierarchical CDL with the 8 block instances.

The flat LVS netlist has the same 11 devices, 5 nets and body connections as
the hierarchical CDL.  netgen independently matches the KLayout target netlist
to the flat schematic (after its normal parallel-device merge) **and** to the
hierarchical CDL with the 8 block instances; both netgen runs report 11 device
instances before the merge and 5 nets.  This is a tool-interface limitation in
the PDK KLayout schematic reader, not a hidden pass.

## Negative control

The script rebuilds the macro with the first PMOS bulk-to-VDD M1 strap removed.
The extracted first PMOS body becomes a floating `$4` net instead of VDD:

```text
M$1 VDD IN OUT $4  pfet_03v3 ...
M$2 VDD IN OUT VDD pfet_03v3 ...
```

KLayout LVS then reports `ERROR : Netlists don't match`, so the body strap is
structurally required by the comparison.

## PEX note

Magic extraction runs:

```text
extract
extresist all
ext2spice cthresh 0
ext2spice extresist on
ext2spice -o <raw>
```

The raw netlist contains 11 MOS devices and 10 capacitors.  `R=0` because this
macro's connections are Metal1/Metal2/Metal3 straps with no resistive
extraction segments; this is a real result for the geometry, not a claim that
the macro has no parasitic resistance.  The four labelled ports are VSS, VDD,
OUT and IN, and every device body node is VSS or VDD.

## Blockers and non-claims

- **Not a full-chip or foundry signoff.**  This is one macro placement with one
  size set.
- **Not full RC parasitic signoff.**  R=0 and the PEX is a single extraction
  point; no corner/sweep/EM/IR analysis.
- **No guard-ring, multi-finger, deep-nwell, or array variants.**
- **PDK LVS reference limitation.**  The LVS reference is flat primitive-M;
  the 8-block hierarchical CDL is supplied for hierarchy, not used as the
  KLayout LVS reference.
- **The generic schematic LUT flow remains scaffold.**  `analog_lut_status:
  scaffold` is retained in the tech YAML.

## Evidence

```text
build/verify/gf180_rx_macro/gf180_rx_macro_evidence.json
build/verify/gf180_rx_macro/gf180_rx_macro.gds
build/verify/gf180_rx_macro/gf180_rx_macro.spice
build/verify/gf180_rx_macro/gf180_rx_macro.cdl
build/verify/gf180_rx_macro/*.log
build/verify/gf180_rx_macro/negative_drop_pmos_bulk/
```

The JSON records exact command arrays, return codes, DRC count, LVS device/net
counts, PEX R/C/X counts, body-net checks and the negative-control result.
