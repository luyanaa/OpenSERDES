# Legacy provenance

This directory preserves the original OpenSERDES digital sources and records
how they map onto the new `rtl/` tree.

- `rtl/serialiser_unit_cell_1.v` — original serializer, copied verbatim.
- `rtl/deserialiser_unit_cell.v` — original deserializer, copied verbatim.

The original layout/SPICE/GDS/PDF artifacts remain in their original folders at
the repository root (`Serializer/`, `DeSerializer/`, `OverSampling_CDR/`,
`Receiver_Bypassing_CDR/`, `Inverter_Based_Tx/`, `Resistive_FB_inverter/`).
The custom `DFF/` and `NAND/` cells now live under `legacy/DFF/` and
`legacy/NAND/`: they are provenance only and are no longer at the repository
root.  The canonical digital flow uses PDK standard-cell flops and NANDs.

## Use rules

- `rtl/` is the canonical, synthesizable digital implementation.
- `legacy/rtl/` is read-only reference; do not add it to a new synthesis or
  lint run.
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
