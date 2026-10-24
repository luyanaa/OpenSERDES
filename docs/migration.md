# Digital migration map

The repository is migrating from the original flat OpenSERDES folders to the
`rtl/` + `tb/` + `constraints/` + `scripts/` + `tech/` structure.  This is a
staged migration: the old folders stay in place until their physical/analog
content is replaced, while all new digital work uses `rtl/`.

## Status

| Original block | Original location | New location | Status | Notes |
|---|---|---|---|---|
| Serializer | `Serializer/serialiser_unit_cell_1.v` | `rtl/serializer.sv` | **Migrated** | 8 × 32-bit frame, LSB/MSB parameter, one-hot `CAPTURE_MODE`; old RTL kept at `legacy/rtl/serialiser_unit_cell_1.v`. |
| Deserializer | `DeSerializer/deserialiser_unit_cell.v` | `rtl/deserializer.sv` + `rtl/deserializer_word.sv` | **Migrated** | No `INT_PAR_IN` shadow latch, no `COMPLETE` clock, one-hot bit/word select; old RTL kept at `legacy/rtl/deserialiser_unit_cell.v`. |
| Oversampling CDR | `OverSampling_CDR/CLK_RECOVERY.*` | `rtl/cdr_oversampling.sv` | **Behavioral only** | New model is a fixed-rate oversampling CDR; the delivered netlist is a digital period tracker/divider. See [`cdr_receiver_digital.md`](cdr_receiver_digital.md). |
| Receiver bypassing CDR | `Receiver_Bypassing_CDR/Receiver.*` | `rtl/receiver_sampler.sv` + RX `CDR_BYPASS` | **Digital boundary migrated** | Analog front-end and 2 GHz custom NAND DFF remain legacy; only the `q <= d` sampling contract is modeled. See [`cdr_receiver_digital.md`](cdr_receiver_digital.md). |
| DFF | `DFF/DFF.sp`, `DFF.gds` | standard-cell flops in synthesis | **Replaced at synthesis** | The new RTL does not instantiate the custom `DFF` cell. |
| NAND | `NAND/NAND.gds` | standard-cell NAND in synthesis | **Replaced at synthesis** | Kept for original physical reference. |
| Inverter-based TX | `Inverter_Based_Tx/` | analog wrapper, not in this repo | **Not migrated** | The digital TX wrapper ends at `tx_serial_out`; the pad/channel driver remains analog. |
| Resistive-feedback inverter | `Resistive_FB_inverter/` | none | **Not migrated** | Analog RX front-end. |

## Compatibility adapters

The old digital module names are available as adapters:

- `rtl/compat/serializer_unit_cell_1.v`
- `rtl/compat/deserialiser_unit_cell_1.v`

They keep the original port list but instantiate the new `serializer` /
`deserializer` cores.  This lets an old integration switch its implementation
without changing its instantiation.

The adapters are **data-path compatible, not cycle-identical**:

- `COMPLETE` is the new one-cycle frame-done / par-valid pulse.
- `COUNT` and `SAMPLE_COUNT` are observability counters reconstructed in the
  adapter; they are not the core's internal state.
- `INTERNAL_FINISH` is derived (word-end / busy), not the old state-machine
  output.
- `OUT` in the deserializer adapter is the last word, not the old scratch
  shift register.

Do not compile an adapter together with its `legacy/rtl/` source: both define
the same module name.

## Verification

`make sim` includes `tb/tb_cdr_bypass.sv` (receiver-only mode) and
`tb/tb_compat_legacy.sv`.  The compatibility test:

1. Serializes a known 8 × 32-bit frame through the compatibility serializer and
   checks all 256 bits against the LSB-first frame order.
2. Feeds the same stream into the compatibility deserializer and checks all
   eight output words plus the completion pulse.

The new cores are also covered by the existing serializer, deserializer,
loopback, channel, and CDR tests.

## Remaining gaps

- The behavioral CDR is not a replacement for the original `CLK_RECOVERY`
  layout/PEX.  No analog or gate-level equivalence is claimed.
- The receiver, inverter-based TX, resistive-feedback inverter, and custom
  DFF/NAND physical cells remain legacy until a new analog/back-end design is
  available.
- The adapters are intended for integration migration.  New designs should
  instantiate `serializer`/`deserializer` directly.
