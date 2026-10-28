# Digital migration map

The repository is migrating from the original flat OpenSERDES folders to the
`rtl/` + `tb/` + `constraints/` + `scripts/` + `tech/` structure.  This is a
staged migration: the old folders stay in place until their physical/analog
content is replaced, while all new digital work uses `rtl/`.

## Status

| Original block | Original location | New location | Status | Notes |
|---|---|---|---|---|
| Serializer | `Serializer/serialiser_unit_cell_1.v` | `rtl/serializer.sv` + `rtl/serializer_stream.sv` | **Migrated** | Parallel/mux and word-streaming architectures; LSB/MSB; one-hot word-group capture; old RTL kept at `legacy/rtl/serialiser_unit_cell_1.v`. |
| Deserializer | `DeSerializer/deserialiser_unit_cell.v` | `rtl/deserializer.sv` + `rtl/deserializer_word.sv` | **Migrated** | No `INT_PAR_IN` shadow latch, no `COMPLETE` clock, one-hot bit/word select; align/flush gated out of capture; old RTL kept at `legacy/rtl/deserialiser_unit_cell.v`. |
| Oversampling CDR | `OverSampling_CDR/CLK_RECOVERY.*` | `rtl/cdr_sync.sv` + `rtl/cdr_oversampling.sv` + `rtl/cdr_adaptive.sv` | **RTL validated** | Fixed-rate, frequency-tracking and source-sync modes. The delivered netlist is a digital period tracker/divider; no gate-equivalence is claimed. See [`cdr_receiver_digital.md`](cdr_receiver_digital.md). |
| Frame alignment | none | `rtl/comma_align.sv` | **New** | Comma/training search, one-shot and per-frame re-alignment, `rx_frame_locked`, flush/lock recovery. |
| Receiver bypassing CDR | `Receiver_Bypassing_CDR/Receiver.*` | `rtl/receiver_sampler.sv` + RX `CDR_MODE=2` | **Digital boundary migrated** | Analog front-end and 2 GHz custom NAND DFF remain legacy; only the `q <= d` sampling contract is modeled. `CDR_BYPASS` remains an alias. See [`cdr_receiver_digital.md`](cdr_receiver_digital.md). |
| DFF | `legacy/DFF/DFF.sp`, `legacy/DFF/Dff.gds` | standard-cell flops in synthesis | **Replaced at synthesis** | The new RTL does not instantiate the custom `DFF` cell. |
| NAND | `legacy/NAND/NAND.gds` | standard-cell NAND in synthesis | **Replaced at synthesis** | Kept for original physical reference. |
| Inverter-based TX | `Inverter_Based_Tx/` | analog wrapper, not in this repo | **Not migrated** | The digital TX wrapper ends at `tx_serial_out`; the pad/channel driver remains analog. |
| Resistive-feedback inverter | `Resistive_FB_inverter/` | none | **Not migrated** | Analog RX front-end. |

## Compatibility adapters

The old digital module names are available as adapters:

- `rtl/compat/serializer_unit_cell_1.v`
- `rtl/compat/deserialiser_unit_cell_1.v`

They keep the original port list **including positional order** and instantiate
the new `serializer` / `deserializer` cores.  `tb_compat_port_order` is a
compile/elaboration check for positional instantiation.  This lets an old
integration switch its implementation without changing its instantiation.

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

`make regress` includes `tb/tb_cdr_bypass.sv` (source-sync mode),
`tb/tb_serdes_channel.sv` (real recovered-serial and parallel-frame
scoreboards), `tb/tb_cdr_adaptive.sv` (0/+700/-700 ppm),
`tb/tb_frame_sync.sv` (comma, gap, flush/re-acquire),
`tb/tb_compat_legacy.sv` and `tb/tb_compat_port_order.sv`.  The compatibility
test:

1. Serializes a known 8 × 32-bit frame through the compatibility serializer and
   checks all 256 bits against the LSB-first frame order.
2. Feeds the same stream into the compatibility deserializer and checks all
   eight output words plus the completion pulse.

The new cores are also covered by the existing serializer, deserializer,
loopback, channel, and CDR tests.

## Generator and qualification

The digital migration now has a product packaging step:

- `spec/*.yaml` is the single source of truth for a configuration.
- `scripts/gen_ip.py` filters architectures from `tech/arch_db.yaml`, emits a
  self-contained RTL/constraints/PnR package and records the selected
  candidate plus its confidence in `manifest.json`.
- `scripts/qualify.py` enforces the cumulative levels `RTL_FUNCTIONAL` ->
  `PRE_PNR_TIMING` -> `POST_ROUTE_TIMING` -> `PHYSICAL_SIGNOFF` ->
  `LINK_VALIDATED`; `scripts/collect_signoff.py` collects STA/DRC/LVS/CDC/BER
  reports into `signoff.json`.
- `scripts/import_ppa.py` fills `tech/ppa_measured.yaml`; `gen_ip.py --ppa-db`
  applies measured gates/metrics before architecture selection.
- `apb_csr`, `async_fifo`, `cdc_sync`, AXIS adapters, AXIS endpoint wrappers,
  PRBS and both single-clock and multi-clock SoC wrappers exist and are tested.
  `phy_ip` remains a scaffold until BIST integration, calibration and DFT exist.

See [`generator.md`](generator.md) and [`qualification.md`](qualification.md).

## Remaining gaps

- The adaptive CDR is a first-order frequency-tracking loop, not a clone of
  the original `CLK_RECOVERY` period tracker.  No analog/gate-level
  equivalence is claimed; jitter tolerance and BER are not measured here.
- APB CSR, async FIFO, cdc_sync, AXIS adapters, AXIS endpoint wrappers,
  PRBS7/15/31, `serdes_bist` and single/multi-clock SoC wrappers are
  implemented.  APB-controlled pad-level BIST injection/loopback and coherent
  counter snapshots are integrated in both wrappers; the single-clock wrapper
  also runs the word/datapath BIST through the AXIS/serializer/RX path.
  Calibration, non-source-sync SoC CDR and CDC-wrapper word BIST remain open.
- The receiver, inverter-based TX, resistive-feedback inverter, and custom
  DFF/NAND physical cells remain legacy until a new analog/back-end design is
  available.
- The adapters are intended for integration migration.  New designs should
  instantiate `serializer`/`deserializer` directly.
