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

`extract_lut.py` refuses to call an alias-only/placeholder file sky130.  It
auto-detects the sky130 PM3 tree under `~/.ciel/ciel/sky130/versions/*/
skywater-pdk/libraries/sky130_fd_pr/latest` (or `SKY130_ROOT=/path/to/latest`):

```bash
make sky130-lut PYTHON=/usr/bin/python3
# trim the sweep:
make sky130-lut PYTHON=/usr/bin/python3 SKY130_TX_LOADS="10 50" SKY130_RX_SLEWS="20 50"
```

A single self-contained model-card file can be used instead:

```bash
python3 verification/analog/extract_lut.py \
  --model /path/to/model_cards.spice \
  --nmos-model sky130_fd_pr__nfet_01v8 \
  --pmos-model sky130_fd_pr__pfet_01v8 \
  --out build/verify/sky130_lut.json
```

The single file must define the device names above or contain real model cards.
Alias-only files such as `SKY130_PDK/models.sp` are rejected with an explicit
error.  The auto-detected PM3 flow includes `models/parameters/invariant.spice`,
`typical.spice`, and the nfet/pfet `__tt.pm3.spice` device subckts.

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

Measured with the CIEL sky130 PM3 tree (tt, 1.8 V, 50 fF, 0.65/1.40 µm
devices): rise ≈ 0.263 ns, fall ≈ 0.187 ns; at 20 ps input slew the inverter
delay is ≈ 0.068/0.082 ns (tPHL/tPLH).  `tau_ns = rise_ns / ln(4)` is the
equivalent first-order RC tau for the 20–80 % rise time.  `scripts/verify_link.py --analog-lut <json> --tx-load-ff 50`
replaces the profile channel tau with this value and records the LUT metadata
in the capability report.

## Limits

- No PEX, no package, no crosstalk, no supply noise, no Monte Carlo.
- No temperature/voltage corners; `VDD` is fixed at 1.8 V.
- The RX table is a single inverter threshold/delay, not a full slicer/CDR.
- PDK model version is recorded only by path, not by content hash yet.
- The generic demo is **not** a sky130 result.
