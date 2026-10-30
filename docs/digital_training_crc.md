# Digital training and CRC capability status

This note records the first post-closure digital additions.  They are
**standalone, verified RTL blocks**, not yet wired into `serdes_digital_top`
or the generated SoC package.  The distinction matters: the generator must not
claim link training or PCS framing until the block is instantiated, has an
actuator, and is included in the package's verification evidence.

## New blocks

| Block | File | Status | Integration |
|---|---|---|---|
| Link-training FSM | `rtl/link_training_fsm.sv` | RTL verified (`tb/tb_link_training_fsm.sv`) | standalone; needs phase/threshold actuator |
| Framed CRC | `rtl/crc_frame.sv` | RTL verified (`tb/tb_crc_frame.sv`) | standalone; needs AXIS/frame plumbing |

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

It is deliberately not connected to `cdr_oversampling` or
`cdr_adaptive`: those blocks currently select phase internally and expose no
external phase/threshold port.  A real integration requires a phase
interpolator or delay-line actuator plus an error source (PRBS checker or
frame CRC).  Until that exists, the generator must report
`training: none` or `training: rtl_standalone`, not `training: verified`.

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

The block is not yet an AXIS frame checker and does not define sequence IDs,
sync words, or retransmission policy.  Those belong in a framed PCS layer, not
inside the CRC primitive.

## Capability matrix

This is the current truth table for the requested roadmap.  `integrated`
means the block is in a generated top-level package; `RTL_STANDALONE` means it
is verified but not connected; `none` means no implementation exists.

| Capability | Options | Current status | Evidence |
|---|---|---|---|
| Line coding | `raw` | integrated RTL | existing serializer/deserializer |
| Line coding | `8b10b`, `64b66b` | none; generator rejects | `test_generator_contracts.py` |
| Framing | `none`, `sync_word` | integrated comma alignment | `rtl/comma_align.sv` |
| Framing / CRC | `crc16`, `crc32` | RTL_STANDALONE | `rtl/crc_frame.sv` |
| Training | `phase_threshold` | RTL_STANDALONE | `rtl/link_training_fsm.sv` |
| Clock source | `source_sync`, `independent` | integrated | `serdes_rx_digital` |
| Clock recovery | fixed/adaptive oversampling | integrated RTL | `cdr_oversampling`, `cdr_adaptive` |
| Clock recovery | bang-bang / PI-CDR / PLL | none | no verified block |
| Sampler | `single_edge` | integrated RTL | `receiver_sampler` |
| Sampler | DDR / multiphase / interleaved | none | needs physical phase clocks |
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
4. `coding: 8b10b` and `coding: 64b66b` remain rejected until encoder,
   decoder, disparity/error handling, comma/gear box semantics, and tests are
   present.
5. Analog macros are separate capability objects.  A closed GF180/IHP macro
   does not by itself prove a digital training axis or a link-level PCS.

## Next implementation slices

1. Add an actuator contract to `cdr_oversampling`/`cdr_adaptive` (external
   phase offset with explicit range/step and a `phase_we` strobe).
2. Connect `link_training_fsm` to the PRBS checker in the SoC/BIST path and
   expose telemetry through `apb_csr`.
3. Add a framed AXIS CRC checker/inserter using `crc_frame`; keep the CRC
   primitive independent.
4. Implement 8b10b encoder/decoder and only then enable `coding: 8b10b`.
