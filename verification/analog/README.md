# Tech-configurable schematic-level LUT extraction

This directory extracts a small **schematic-level** LUT with ngspice.  The
technology is selected by a YAML config under `verification/analog/tech/`; the
extractor has no PDK-specific branches.  The shipped `sky130` config is the
supported analog target; `gf180mcu` and `ihp_sg13g2` are explicit scaffolds
because their analog migration is not done.  It is deliberately not a PEX or silicon flow: the netlists use the
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

## Tech configs

| Config | Status | Notes |
|---|---|---|
| `tech/sky130.yaml` | supported | PM3 params + nfet/pfet `__tt.pm3.spice`; Magic/KLayout PEX paths |
| `tech/gf180mcu.yaml` | scaffold | analog device/model migration not done; refused unless `--allow-scaffold` |
| `tech/ihp_sg13g2.yaml` | scaffold | same |
| `tech/generic_demo.yaml` | demo | ngspice level-1 MOSFETs; never PDK evidence |

Inspect and check the resolved paths/tools:

```bash
python3 verification/analog/extract_lut.py --list-tech
make analog-tech-check ANALOG_TECH=sky130 PYTHON=/usr/bin/python3
python3 verification/analog/extract_lut.py --tech sky130 --check-tools --out /tmp/sky_check.json
```

The sky130 config auto-detects the PM3 tree under
`~/.ciel/ciel/sky130/versions/*/skywater-pdk/libraries/sky130_fd_pr/latest`
(or `SKY130_ROOT`).  Magic is invoked with the config's `sky130A.magicrc` and
`PDK_ROOT` pointing at the version directory; KLayout's `.lyt`/`.lyp` paths are
resolved from the same tree.  This is path discovery only: the LUT flow does
not consume PEX yet.

```bash
make sky130-lut PYTHON=/usr/bin/python3
make analog-lut ANALOG_TECH=sky130 PYTHON=/usr/bin/python3
# trim the sweep:
make analog-lut ANALOG_TECH=sky130 PYTHON=/usr/bin/python3 \
  ANALOG_TX_LOADS="10 50" ANALOG_RX_SLEWS="20 50"
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

## Adding a PDK

1. Copy an existing tech YAML.
2. Set `pdk`, `status`, `vdd_v`, device style/names, sizing and the model
   include list/root search patterns.
3. Fill the `pex.magic`/`pex.klayout` relative paths.
4. Run `--check-tools` first, then a tiny `--tx-loads-ff 50 --rx-slews-ps 20`
   sweep; only mark `status: supported` after the model tree resolves.
5. Update `scripts/verify_link.py` handling only if a new model kind is needed;
   the gate already accepts any `model_kind` starting with `sky130-schematic`
   for the `analog_model_validated` flag (extend that list for new PDKs).

## Generic demo mode (not a PDK)

`--tech generic_demo` (or legacy `--demo`) uses ngspice built-in level-1
MOSFETs so the flow is runnable without a PDK install:

```bash
make analog-lut-demo PYTHON=/usr/bin/python3
```

The JSON is marked `"pdk": "generic", "model_kind": "generic-demo"` and must
not be reported as PDK data.

## PVT characterization

`pvt_runner.py` sweeps the configured schematic corners x voltages x
temperatures at one fixed load/slew and records continuous measurements:

```bash
make analog-tech-check ANALOG_TECH=sky130 PYTHON=/usr/bin/python3
make sky130-pvt PYTHON=/usr/bin/python3
# smaller matrix:
python3 verification/analog/pvt_runner.py --corners tt ss --voltages 1.62 1.98 \
  --temperatures -40 100 --out build/verify/pvt_small.json
```

Default sky130 matrix: `tt/ss/ff/sf/fs` x `1.62/1.80/1.98 V` x
`-40/25/100 C` (45 points, one TX and one RX ngspice run each).  The official
corner entry points must be checked against the target PDK release; this runner
uses the PM3 corner device files plus `invariant.spice` so it does not mix
`sky130.lib.spice` corner settings with PM3 device parameters.  A full 45-point
run is slow (model loading dominates); each point has its own work directory and
failure record.  No PEX or Monte Carlo is included in this row set.

The output is `build/verify/sky130_pvt_lut.json` with rows such as
`tx_rise_ns`, `tx_fall_ns`, `tx_swing_v`, `rx_tphl_ns`, `rx_tplh_ns`, plus the
corner/voltage/temperature labels, ngspice version and git commit.

## Monte Carlo

`mc_runner.py` defaults to a **behavioral** local-mismatch study: independent
series gate offsets for the NMOS/PMOS are sampled, and each sample records the
joint offset + TX/RX measurement vector.  This is a screening/sensitivity tool,
not a PDK statistical model.

```bash
make sky130-mc PYTHON=/usr/bin/python3 MC_SAMPLES=20
python3 verification/analog/mc_runner.py --mode pdk --dry-run  # fails closed here
```

The PM3 tree ships mismatch corner parameters but no verified statistical
switch was found in this installation, so `--mode pdk` refuses to run rather
than relabeling behavioral offsets as PDK MC.  A single sample costs the same
as a PVT point (two ngspice runs), so 20 samples is already a long local run.

## PEX

`pex_characterize.py` has two explicit modes:

```bash
# Compare an externally extracted PEX LUT against the schematic LUT
make pex-compare SCHEM_LUT=build/verify/sky130_lut.json \
  PEX_LUT=build/verify/sky130_pex_lut.json PEX_OUT=build/verify/pex_delta.json

# Run the same TX/RX testbench on an extracted subcircuit netlist
python3 verification/analog/pex_characterize.py --pex-netlist tx_pex.sp \
  --top tx_driver --ports in=in,out=out,vdd=vdd,vss=vss \
  --loads-ff 50 --out build/verify/tx_pex_lut.json
```

The PEX netlist must already be DRC/LVS-clean generated and carry its own
models/parasitics.  This repository does not generate a GDS, run Magic
`extresist` or claim post-layout signoff automatically; the Magic/KLayout paths
are resolved by `--check-tools` for an explicit next step.

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
