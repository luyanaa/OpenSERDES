# sky130 schematic-level LUT extraction

This directory extracts a small **schematic-level** LUT from the sky130 PDK with
ngspice.  It is deliberately not a PEX or silicon flow: the netlists use the
PDK device models with ideal voltage sources and an ideal load capacitor.  The
result is suitable for seeding the behavioral channel (driver rise time -> RC
tau) and for a sampling-delay budget, not for a PDK signoff claim.

## What is extracted

| Table | Sweep | Outputs |
|---|---|---|
| TX driver | load capacitance (10/50/100/200 fF) | 20–80 % rise/fall, Vmax/Vmin, swing |
| RX inverter slicer | input slew (20/50/100/200 ps) | tPHL/tPLH at VDD/2 |

Netlists use a 1.8 V inverter with default sky130 sizing
(`WN=0.65 µm`, `WP=1.40 µm`, `L=0.15 µm`); override with `--wn-um`, `--wp-um`,
`--l-um`.

## Real sky130 model card

`extract_lut.py` refuses to call an alias-only/placeholder file sky130.  Point
it at a real model file:

```bash
export SKY130_MODEL=/path/to/sky130A/libs.ref/sky130_fd_pr/spice/sky130_fd_pr__models.spice
make sky130-lut PYTHON=/usr/bin/python3
# or
python3 verification/analog/extract_lut.py \
  --model "$SKY130_MODEL" \
  --nmos-model sky130_fd_pr__nfet_01v8 \
  --pmos-model sky130_fd_pr__pfet_01v8 \
  --out build/verify/sky130_lut.json
```

The model file must define the device names above (or pass real names with
`--nmos-model`/`--pmos-model`).  The script checks for `level=`, `vto=` or
`kp=` in the `.model` cards; the alias file sometimes shipped as
`SKY130_PDK/models.sp` is rejected with an explicit error.

## Generic demo mode (not sky130)

`--demo` uses ngspice built-in level-1 MOSFETs so the flow is runnable without
a PDK install:

```bash
make analog-lut-demo PYTHON=/usr/bin/python3
```

The JSON is marked `"pdk": "generic", "model_kind": "generic-demo"` and must
not be reported as sky130 data.

## Consuming the LUT

```python
from verification.analog.lut import load_lut, tx_tau_ns
lut = load_lut("build/verify/sky130_lut.json")
tau_ns = tx_tau_ns(lut, load_ff=50)
```

`tau_ns = rise_ns / ln(4)` is the equivalent first-order RC tau for the
20–80 % rise time.  `scripts/verify_link.py --analog-lut <json> --tx-load-ff 50`
replaces the profile channel tau with this value and records the LUT metadata
in the capability report.

## Limits

- No PEX, no package, no crosstalk, no supply noise, no Monte Carlo.
- No temperature/voltage corners; `VDD` is fixed at 1.8 V.
- The RX table is a single inverter threshold/delay, not a full slicer/CDR.
- PDK model version is recorded only by path, not by content hash yet.
- The generic demo is **not** a sky130 result.
