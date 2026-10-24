# Receiver bypassing CDR — not migrated

This folder is the original analog / resistive-feedback receiver evidence
(GDS, SPICE, PEX, layout and test images).

The new digital tree does not replace this front-end.  The digital RX endpoint
(`rtl/serdes_rx_digital.sv`) starts at the serial input and ends at parallel
data; the analog receiver remains legacy until a new front-end is designed and
verified.  See `docs/migration.md`.
