# Legacy provenance

This directory preserves the original OpenSERDES digital sources and records
how they map onto the new `rtl/` tree.

- `rtl/serialiser_unit_cell_1.v` — original serializer, copied verbatim.
- `rtl/deserialiser_unit_cell.v` — original deserializer, copied verbatim.

The original layout/SPICE/GDS/PDF artifacts remain in their original folders at
the repository root (`Serializer/`, `DeSerializer/`, `OverSampling_CDR/`,
`Receiver_Bypassing_CDR/`, `DFF/`, `NAND/`, `Inverter_Based_Tx/`,
`Resistive_FB_inverter/`).  They are not moved because they are large and are
still the only physical evidence for the original Sky130 implementation.

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
