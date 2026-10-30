# Digital training and CRC capability status

This note records the first post-closure digital additions.  They are
**standalone, verified RTL blocks**, not yet wired into `serdes_digital_top`
or the generated SoC package.  The distinction matters: the generator must not
claim link training or PCS framing until the block is instantiated, has an
actuator, and is included in the package's verification evidence.

## New blocks

| Block | File | Status | Integration |
|---|---|---|---|
| Link-training FSM | `rtl/link_training_fsm.sv` | RTL verified (`tb/tb_link_training_fsm.sv`) | optional `TRAINING_ENABLE` in `serdes_digital_top` and `serdes_soc_cdc_wrapper`; APB CSR 13/14 telemetry; external error count required |
| Framed CRC | `rtl/crc_frame.sv` | RTL verified (`tb/tb_crc_frame.sv`) | primitive |
| AXIS CRC insert/check | `rtl/axis_crc_inserter.sv`, `rtl/axis_crc_checker.sv` | RTL verified (`tb/tb_axis_crc.sv`) | optional `CRC_ENABLE` in the AXIS TX/RX endpoints; `tuser` marks payload end, `tlast` marks final CRC |
| 8b10b encoder/decoder | `rtl/pcs_8b10b_tx.sv`, `rtl/pcs_8b10b_rx.sv` | RTL verified (`tb/tb_pcs_8b10b.sv`) | standalone PCS wrappers; all 256 data bytes + K28.5 |
| 64b66b blocks | `rtl/pcs_64b66b.sv` | RTL verified (`tb/tb_64b66b.sv`) | data + control-header blocks; 66-to-64 gearbox (`tb/tb_64b66b_gearbox.sv`) |

### `link_training_fsm`

The FSM scans a phase axis, then a threshold axis, using a monotonic error
counter.  It captures the counter at the start of each dwell window, compares
the delta, freezes the best phase/threshold, and monitors the error rate while
locked.  A sustained error increase, or a new `start` pulse, requests retrain
and restarts the scan automatically.

Interface contract:

```systemverilog
link_training_fsm #(
  .PHASE_STEPS(8), .THRESH_STEPS(8),
  .DWELL_CYCLES(64), .ERROR_WIDTH(16),
  .RETRAIN_ERR_THRESH(4), .RETRAIN_WINDOW(256)
) u_train (
  .clk, .rst_n, .enable, .start,
  .error_count,              // free-running monotonic error count
  .phase_sel, .thresh_sel,   // actuator requests
  .phase_we, .thresh_we,     // one-cycle load strobes
  .busy, .locked, .retrain, .state,
  .best_phase, .best_thresh, .best_errors
);
```

`cdr_oversampling` and `cdr_adaptive` accept a bounded external `phase_offset`
(wrapping over their phase period).  `serdes_digital_top` and
`serdes_soc_cdc_wrapper` have optional `TRAINING_ENABLE` paths that connect the
FSM phase request to that input.  The SoC wrapper exposes training status in
APB read-only registers 13 (`best_errors[7:0]`) and 14 (`locked`, `busy`,
`retrain`, current phase, best phase); `tb_soc_cdc_training` checks the CSR
path.  The error counter is still an external monotonic input
(`train_error_count`) and is not yet connected to the SoC BIST checker.  The
generator still does not expose the training parameter as a capability, so the
link-level claim remains `RTL_OPTIONAL`, not `training: verified`.

### `crc_frame`

The CRC is bit-serial and parameterized for CRC-16/CCITT-FALSE and
CRC-32/ISO-HDLC.  `frame_start` loads `INIT`, each `data_valid` word shifts the
CRC, and `frame_end` marks the final word and raises `crc_valid` for one cycle.
`crc_raw` exposes the unmasked accumulator; `crc` applies optional reflection
and final XOR.

Verified vectors:

```text
CRC-16/CCITT-FALSE("123456789") = 0x29B1
CRC-32/ISO-HDLC("123456789")    = 0xCBF43926
```

`axis_crc_inserter` appends the CRC beats after the payload and uses
`m_axis_tuser` to mark the last payload beat; `m_axis_tlast` is reserved for
the final CRC beat.  `axis_crc_checker` forwards payload, consumes the CRC
beats, and pulses `crc_ok`/`crc_error`.  `serdes_tx_axis_digital` and
`serdes_rx_axis_digital` expose `CRC_ENABLE` and extend the serialized frame
by `ceil(CRC_WIDTH/DATA_WIDTH)` words.  `tb_crc_tx_endpoint` and
`tb_crc_rx_endpoint` verify the 4-word payload plus CRC16 path.  The SoC async
wrapper does not yet expose a CRC-enable CSR bit; sync words, sequence IDs and
retransmission policy remain outside the CRC block.

## Capability matrix

This is the current truth table for the requested roadmap.  `integrated`
means the block is in a generated top-level package; `RTL_STANDALONE` means it
is verified but not connected; `none` means no implementation exists.

| Capability | Options | Current status | Evidence |
|---|---|---|---|
| Line coding | `raw` | integrated RTL | existing serializer/deserializer |
| Line coding | `8b10b` | RTL_STANDALONE (256 data + K28.5) | `rtl/encoder_8b10b.sv`, `rtl/decoder_8b10b.sv` |
| Line coding | `64b66b` | RTL_STANDALONE data block only; no control/gearbox | `rtl/pcs_64b66b.sv` |
| Line coding | generator selection | none; rejects both | `test_generator_contracts.py` |
| Framing | `none`, `sync_word` | integrated comma alignment | `rtl/comma_align.sv` |
| Framing / CRC | `crc16`, `crc32` | RTL_STANDALONE | `rtl/axis_crc_inserter.sv`, `rtl/axis_crc_checker.sv` |
| Training | `phase_threshold` | RTL_OPTIONAL (top parameter, external error count) | `rtl/link_training_fsm.sv` |
| Clock source | `source_sync`, `independent` | integrated | `serdes_rx_digital` |
| Clock recovery | fixed/adaptive oversampling | integrated RTL | `cdr_oversampling`, `cdr_adaptive` |
| Clock recovery | bang-bang / PI-CDR / PLL | none | no verified block |
| Sampler | `single_edge` | integrated RTL | `receiver_sampler` |
| Sampler | DDR / multiphase / interleaved | fail-closed in generator | needs physical phase clocks |
| Clock axes | `clock_source`, `clock_recovery`, `sampler_architecture` | explicit spec validation + manifest | `gen_ip.py` |
| TX driver | RTL serializer | integrated | `serializer*.sv` |
| TX driver | inverter / segmented / CML | analog macros only where separately closed | analog evidence directories |
| RX front-end | RTL sampler | integrated | `receiver_sampler` |
| RX front-end | resistive-FB / comparator / CTLE | macro-level only where separately closed | analog evidence directories |
| Equalization | none | only supported option | no CTLE/DFE RTL |
| Termination | none / high-Z | not modeled in RTL | analog/IO contract required |
| Lane bonding | single lane | integrated | current top |
| Lane bonding | 2/4 lane | none | no deskew block |
| Power management | none | only supported option | no gated/sleep controller |
| FEC | none | only supported option | no FEC block |

## Manifest capability report

`gen_ip.py` now writes a conservative `manifest.capabilities` block for every
generated package.  It records the selected option, the implementation class
(`rtl`, `rtl_standalone`, or `none`), and whether the block is actually
integrated in the generated design.  For example, the default package reports:

```json
"training": {
  "selected": "none",
  "implementation": "rtl_standalone",
  "module": "link_training_fsm",
  "integrated": false
}
```

This is a status report, not permission to claim the feature.  A block stays
`integrated: false` until it is in the package file list and has package-level
evidence.

## Capability requests in the spec

`capabilities` is an optional spec mapping.  It uses the same vocabulary as
the manifest report and is checked for consistency with `link.coding`,
`rx.clocking`, `rx.align`, and the selected CDR architecture.  For example:

```yaml
capabilities:
  coding: raw
  framing: sync_word
  training: none          # explicit bypass, not a fake training block
  equalization: none
  termination: none
```

A request for a standalone or unimplemented option is rejected.  For example,
`training: phase_threshold` currently fails with:

```text
capabilities.training='phase_threshold' is RTL_STANDALONE and is not
integrated into the generated package; use the explicit bypass `none`
```

This preserves the rule that a package may declare a capability only when the
block is wired into the generated design and has package-level evidence.

## Rules for the generator

1. A requested capability may only be marked `integrated` if the block is in
   `DESIGN_FILES`, appears in the generated file list, and has a passing
   package-level verification report.
2. `RTL_STANDALONE` is a valid source status, but it is not a link capability.
   It must not change `coding`, `framing`, `training`, or `qualification` in the
   generated manifest.
3. Missing actuators must be `none`, not a synthesized fake phase/threshold
   control.  In particular, do not connect `phase_sel` to a CDR that has no
   phase input.
4. `coding: 8b10b` and `coding: 64b66b` remain rejected by the generator until
   the verified encoder/decoder primitives are integrated into the selected
   serializer data path (including the 10-bit 8b10b serializer and 66-bit
   block distribution/lane alignment) and package-level tests exist.  The
   standalone blocks are not sufficient to change the link-level coding claim.
5. Analog macros are separate capability objects.  A closed GF180/IHP macro
   does not by itself prove a digital training axis or a link-level PCS.

## Next implementation slices

1. Connect `train_error_count` to the SoC PRBS checker and expose training
   telemetry through `apb_csr`; only then allow `training: phase_threshold` in
   the generator.
2. Add an integrated AXIS CRC mode (including `tuser` plumbing) to the SoC
   wrapper and decide the on-wire CRC order/sequence-ID contract.
3. Add 64b66b control blocks, block distribution and a gearbox; only then
   enable `coding: 64b66b`.
4. Add a physical phase-clock generator / sampler backend before enabling DDR,
   multiphase or interleaved sampling.
