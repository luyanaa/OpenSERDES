# Oversampling CDR — behavioral replacement only

The new digital model is `rtl/cdr_oversampling.sv` plus the RX integration in
`rtl/serdes_rx_digital.sv`.

This folder remains the original Sky130 `CLK_RECOVERY` GDS/LEF/SPICE/PEX/PDF
evidence.  The new model is **not** a transistor- or layout-equivalent clone of
the original hard macro; see `docs/migration.md`.

Do not use the new behavioral CDR as analog/PEX signoff evidence.
