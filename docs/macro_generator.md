# Cross-process analog macro generator

`verification/analog/macro_gen.py` generates the portable circuit/contract
layers:

```text
topology (inverter chain / feedback inverter)
  -> tech YAML device binding
  -> circuit.spice + macro.v + manifest.json
  -> optional explicit PDK layout template
```

It deliberately does **not** invent layout, LEF or Liberty.  Physical views are
`missing` unless an explicit `--layout-gds` template is supplied.  Supplied
layouts are audited for transistor layers (`diff`, `poly`, `li1`); an
abstract/pin-only GDS is rejected instead of being advertised as signoff.

## Supported now

```bash
python3 verification/analog/macro_gen.py \
  --tech sky130 --topology tx_inv_chain \
  --stages 1,3,9 --out generated/sky130/tx_inv_chain
```

- sky130: supported schematic + black-box Verilog generation
- gf180mcu / IHP SG13G2: `status: scaffold`; generation fails closed until
  device model names, W/L ranges and layout templates are verified.

The TX generator preserves the 1/3/9 NMOS and 2/6/18 PMOS stage ratios.
The RX generator preserves the three-parallel NMOS / six-parallel PMOS
inverter and two series diode-connected long-channel PMOS feedback devices.

## Physical layout contract

A physical template must contain real diff/poly/li1 device layers, not only
met1 pin rectangles.  For the current repository GDS this audit fails:

```text
tx_inv_chain_1_3_9: missing diff, poly, li1
rx_res_fb_inv:     missing diff
```

That is intentional.  The schematic generator remains useful for device
binding and contract generation, while physical signoff stays fail-closed.

## Portability rules

- Device names, supply, W/L and corner data come from `verification/analog/tech`.
- No absolute PDK-version path is embedded in generated manifests.
- No PDK without a supported device binding and layout template is silently
  substituted with an ideal resistor or a generic inverter.
- The generated `macro.v` is a black box; timing must not be inferred from it.
