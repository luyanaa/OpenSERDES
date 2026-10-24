# Serializer — migrated

The canonical digital serializer is now:

- `rtl/serializer.sv`
- `rtl/compat/serializer_unit_cell_1.v` (legacy port adapter)

The original RTL is preserved verbatim at
`legacy/rtl/serialiser_unit_cell_1.v`.

This folder keeps the original Sky130 GDS/LEF/SPICE/PDF artifacts.  See
`docs/migration.md` for the migration status and remaining gaps.
