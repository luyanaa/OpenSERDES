# Legacy provenance

This directory preserves the original OpenSERDES digital sources and records
how they map onto the new `rtl/` tree.

- `rtl/serialiser_unit_cell_1.v` — original serializer, copied verbatim.
- `rtl/deserialiser_unit_cell.v` — original deserializer, copied verbatim.

The original analog/layout artifacts now live under `legacy/analog/`:
`Inverter_Based_Tx/`, `OverSampling_CDR/`, `Resistive_FB_inverter/` and
`Receiver_Bypassing_CDR/` (GDS, SPICE, PEX, LVS, PDF and screenshots).  They
are provenance only and are not used by the digital flows; the analog macros
used by the current flows live in `analog/macros/` and are verified PCell
macros, not regenerated from these folders.  The custom `DFF/` and `NAND/`
cells live under `legacy/DFF/` and `legacy/NAND/` for the same reason.  The
canonical digital flow uses PDK standard-cell flops and NANDs.

## Use rules

- `rtl/` is the canonical, synthesizable digital implementation.
- `legacy/rtl/` is read-only reference; do not add it to a new synthesis or
  lint run.
- `legacy/analog/` is read-only provenance; do not add its GDS/SPICE/PEX/LVS
  files to a new analog DRC/PEX/PnR flow.  Current analog macros live in
  `analog/macros/`.
- `rtl/compat/` contains adapters with the original module names.  Compile
  either the adapter or the legacy RTL, never both: the module names collide.
- The original analog/back-end blocks are not equivalent to the behavioral
  `rtl/cdr_oversampling.sv`; see [`../docs/migration.md`](../docs/migration.md).

## Why the old RTL is not the canonical source

The original serializer/deserializer used a shared `OUT`/`INT_PAR_IN` scratch
bank, a `COMPLETE`-derived clock, and a binary word/bit select.  The migrated
RTL removes the scratch bank and the derived clock, uses one-hot word/bit
select, and adds parameterized `WORDS`/`DATA_WIDTH` plus a capture-mode input
register.  The old files remain useful for provenance and for comparing
behaviour, not for new synthesis.
