# Unified 3-PDK analog macro package

`make analog-macro-package` ingests the analog macros available on each PDK and
emits one common package under `out/macro_package_3pdk/` with a single manifest
schema and a package-level `package_report.json`.

## What it does

This is a **contract + evidence unification** step, not new physical
generation.  For each macro it:

- copies the macro's real artifacts (GDS / LEF / CDL / PEX / schematic / DRC /
  LVS reports) into `out/macro_package_3pdk/<name>/`, so verified evidence is
  persisted out of ephemeral `/tmp` working dirs;
- normalises each macro to the manifest schema shared with
  `verification/analog/macro_gen.py`: `canonical_ports`, `named_pins`
  (power/ground/input/output), `device_models`, `topology`, `ports`;
- records per-view `sha256` and a macro-level `evidence_hash` (SHA256 over the
  macro name + tech + the bytes of every available view), so identical content
  is identically hashed regardless of which flow produced it;
- reports `status` and `physical_signoff_eligible` honestly — a macro is
  SIGNOFF_ELIGIBLE only when GDS + clean DRC + unique-match LVS + PEX are all
  present for that exact macro.

## Macros in the package (this checkout)

| Macro | PDK | Source | Status |
|---|---|---|---|
| `ihp_rx_macro` | ihp_sg13g2 | `verification/analog/ihp_rx_macro.py` (KLayout PCell + Magic PEX + netgen LVS) | SIGNOFF_ELIGIBLE |
| `gf180_rx_macro` | gf180mcu | `verification/analog/gf180_rx_macro.py` (KLayout PCell + Magic PEX + netgen/KLayout LVS) | SIGNOFF_ELIGIBLE |
| `sky130_tx_inv_chain` | sky130 | `verification/analog/macro_gen.py` (generated contract + schematic) | SCHEMATIC_ONLY |

The two `rx_macro` cells are signoff-eligible because their builders already
ran KLayout PCell layout, Magic DRC (clean), netgen/KLayout LVS ("circuits
match uniquely") and Magic PEX, and the artifacts are copied into the package.
`sky130_tx_inv_chain` is honestly `SCHEMATIC_ONLY` because the generator emits
a contract + schematic but no physical views; attaching its GDS/LEF/PEX (e.g.
via `make magic-pex`) would upgrade it.

## Files

- `verification/analog/macro_pack_3pdk.py` — the packager (`--spec`, `--out`,
  `--report`).
- `verification/analog/macro_package_3pdk.json` — the ingest spec listing each
  macro's tech, ports, builder, evidence path, DRC/LVS verdicts and artifact
  candidates.
- `out/macro_package_3pdk/<name>/manifest.json` + copied views.
- `out/macro_package_3pdk/package_report.json` — the 3-PDK summary.

## No overclaiming

Packaging does not upgrade status: a macro is only `SIGNOFF_ELIGIBLE` when the
referenced DRC/LVS/PEX artifacts actually exist and were verified by its own
builder.  `macro_generation` for gf180/ihp remains `false` in the tech YAMLs —
these are *bespoke verified* macros packaged into the unified schema, not
newly-generated signoff macros.

### Host-dependent verdicts

`build/` is gitignored, so the evidence records the packager hashes only exist
on a host where `verification/analog/gf180_rx_macro.py` /
`ihp_rx_macro.py` were actually run.  On such a host the gf180 and ihp macros
report `SIGNOFF_ELIGIBLE` because their evidence shows real Magic DRC clean,
netgen/KLayout LVS match, and RC-PEX extraction (the gf180 PEX netlist carries
extracted `pfet_03v3`/`nfet_03v3` devices plus parasitic capacitors).  On a
fresh CI checkout with no `build/verify/` artifacts the same spec returns
`UNVERIFIED` — the gating is fail-closed, so the `SIGNOFF_ELIGIBLE` verdict in
the table is a property of this verified workspace, not a config claim.
