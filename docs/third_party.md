# Third-party source notes

## 8b/10b encoder/decoder

`rtl/encoder_8b10b.sv` and `rtl/decoder_8b10b.sv` are adapted from the
`libsv` project:

- Source: https://github.com/bensampson5/libsv
- SPDX license: MIT
- Files: `libsv/coders/encoder_8b10b.sv`, `libsv/coders/decoder_8b10b.sv`

The local wrappers keep the original lookup-table implementation and are
compiled by the native SystemVerilog/iverilog and Verilator paths.  The
repository-level GPL-3.0 license does not replace the MIT terms of those
adapted files; retain the SPDX header when redistributing them.

All other new digital blocks in this change (`link_training_fsm`, `crc_frame`,
`axis_crc_inserter`, `axis_crc_checker`, `pcs_64b66b`) are original
OpenSERDES code.
