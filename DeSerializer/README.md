# Deserializer — migrated

The canonical digital deserializer is now:

- `rtl/deserializer.sv`
- `rtl/deserializer_word.sv`
- `rtl/compat/deserialiser_unit_cell_1.v` (legacy port adapter)

The original RTL is preserved verbatim at
`legacy/rtl/deserialiser_unit_cell.v`.  The original `INT_PAR_IN`/`COMPLETE`
scratch-bank behaviour is intentionally not preserved; see
`docs/migration.md`.

This folder keeps the original Sky130 GDS/LEF/SPICE/PDF artifacts.
