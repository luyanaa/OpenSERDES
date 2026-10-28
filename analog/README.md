# Analog macro contracts

Canonical pin order is always:

```text
VDD, VSS, IN, OUT
```

Never instantiate the legacy source or layout SPICE by positional order.  The
views use different historical orders:

- TX schematic/layout: `GND VDD VIN VOUT`
- RX schematic: `GND INP OUT VDD`
- RX layout: `GND VDD INP OUT`

`canonical.spice` and `macro.v` in each macro directory are the downstream
interfaces.  `manifest.json` is the source of truth for aliases, polarity,
topology, view status and evidence.  Missing LEF/Liberty or non-ngspice PEX is
recorded as missing/unusable; it is not fabricated.

Validate every contract with:

```bash
python3 verification/analog/macro_contract.py --validate
```


## Generated views

`make analog-views` produces `abstract.lef` and `macro.lib` beside each
manifest.  The LEF pins are abstract rectangles derived from GDS text labels,
not extracted metal shapes.  The Liberty is an analog black box with no timing
arcs; it must not be treated as a characterized digital timing model.

`make analog-package PACKAGE=...` stages the views into the package and writes
`analog/librelane_macros.yaml`.  The PnR config is not overwritten
automatically; merge the fragment only after checking the target LibreLane
MACROS schema.
