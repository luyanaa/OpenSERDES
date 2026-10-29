# Analog macro contracts (Milestone A)

The analog macro set now has canonical, name-based interfaces.  The RX identities are intentionally separate:

| Macro | Topology | Polarity | Canonical pins |
|---|---|---|---|
| `tx_inv_chain_1_3_9` | three-stage increasing CMOS inverter chain (1/2, 3/6, 9/18 N/P) | inverting | `VDD, VSS, IN, OUT` |
| `rx_parallel_inv_macro` | three parallel stdcell inverters | inverting | `VDD, VSS, IN, OUT` |
| `rx_res_fb_pmos` | CMOS inverter + two series diode-connected W=0.55/L=8 µm PMOS feedback | inverting | `VDD, VSS, IN, OUT` |

The canonical pin order is **always** `VDD, VSS, IN, OUT`.

Historical positional orders are explicitly recorded and validated:

```text
TX schematic/layout: GND VDD VIN VOUT
RX schematic:       GND INP OUT VDD
RX layout:          GND VDD INP OUT
```

Downstream tools must instantiate `canonical.spice` or `macro.v`, never the
legacy file by position.  `manifest.json` holds aliases, topology, polarity,
view status, evidence and missing-view reasons.

## Views and status

- `tx_inv_chain_1_3_9`:
  - physical stdcell GDS/LEF/canonical PEX: available
  - Magic DRC: clean (0 errors)
  - netgen LVS: match unique
- `rx_parallel_inv_macro`:
  - physical 3-parallel stdcell GDS/LEF/canonical PEX: available
  - Magic DRC: clean; netgen LVS: match unique
- `rx_res_fb_pmos`:
  - physical full GDS (inverter + two series nonlinear PMOS): available
  - Magic DRC: clean; Calibre LVS: CORRECT; Magic RC PEX: available
- Liberty remains a black box without timing arcs.  Timing must not be inferred
  from `macro.lib`; C uses the soft-stdcell implementation for digital STA.

## Commands

```bash
make analog-contracts
python3 verification/analog/macro_contract.py --validate
make analog-views       # KLayout abstract LEF + black-box Liberty
make analog-package     # stage views into out/ci-soc/analog
make analog-drc
make analog-macro-pex
make analog-rx-dc        # tt DC/PEX
make analog-rx-pvt       # 5 corners + local MC (longer)
make magic-pex
```

The contract validator checks unique canonical names, alias coverage, every
view's port order, required view presence, path existence, polarity verification
and that no non-ngspice PEX is marked available.  Negative tests cover missing
aliases, duplicate/unknown view pins and Calibre RVE relabeling.

## RX macro baseline evidence

`make analog-rx-dc        # tt DC/PEX
make analog-rx-pvt       # 5 corners + local MC (longer)` extracts the RX feedback GDS with Magic, converts the
schematic MOS cards to sky130 devices, and compares DC trip point/slope at
tt/ss/ff/sf/fs.  Initial tt DC result: PEX trip 0.839 V, schematic/PEX `max_abs_v=1.57e-5`,
`trip_delta=-2.2e-7 V`.  AC at tt: PEX gain about 17.05 dB at 1 MHz and
10 MHz; no 3 dB crossing below 1 GHz in this first sweep.  Local mismatch and
DC corner sweeps are implemented; noise/MC signoff remains open.

## C: macro views and package staging

`make analog-views` generates:

- `abstract.lef`: macro size and **GDS met1.pin (68/16) rectangles**.  The pin
  rectangles are now extracted from the GDS, not label-only boxes; the file
  name remains `abstract.lef` for compatibility.
- `macro.lib`: analog black-box Liberty with pins and area, **no timing arcs**.
- `views.json`: bbox, pin coordinates and generation provenance.

`make analog-package` copies the canonical SPICE, GDS, LEF, Liberty and
black-box Verilog into `<package>/analog/` and writes
`<package>/analog/librelane_macros.yaml`.  It deliberately does not overwrite
`pnr/librelane.yaml`; the fragment must be merged after confirming the target
LibreLane MACROS schema and actual macro instantiation.

## Polarity evidence

DC sweeps with the sky130 ngspice models show both macros invert:

- TX: VIN high → VOUT low; odd-stage inverter chain.
- RX feedback inverter: INP high → OUT low; threshold near the inverter trip
  point.  The feedback is nonlinear PMOS, not a linear resistor.
