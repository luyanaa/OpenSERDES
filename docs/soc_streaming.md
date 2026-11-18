# SoC integration: AXI4-Stream + APB streaming peripheral

`serdes_soc_cdc_wrapper` is the entry point for a real SoC: a standard
AXI4-Stream data plane, an APB control plane, and all SerDes clock-domain
crossings inside.  This note records the integration contract and the one
protocol adaptation layer that was still missing.

```
                 s_axis_aclk          tx_clk                 rx_clk         m_axis_aclk
DMA / MAC  ──AXIS──▶ [framer] ──▶ async_fifo ──▶ adapter ──▶ SER ─╮
                                                                  │  (line)
CPU ──APB──▶ CSR0/7/8-15                                     SER ─╯
                                                                  │
                  ┌── [deframer] ◀── async_fifo ◀── adapter ◀── RX ┘
                  └──AXIS──▶ SoC
```

## Data plane

| port | domain | meaning |
|---|---|---|
| `s_axis_tdata/tvalid/tlast/tready` | `s_axis_aclk` | upstream TX stream |
| `m_axis_tdata/tvalid/tlast/tready` | `m_axis_aclk` | downstream RX stream |

`FIFO_DEPTH` sets the elastic depth on both sides.  A source-synchronous link
(`CDR_MODE=2`, `OVERSAMPLE=1`) is what the SoC ports exist for; the recovered
clock is `rx_clk`.

### The framing contract, and `PKT_ENABLE`

Without `PKT_ENABLE` the internal contract applies directly to the SoC port:

- each TX frame is exactly `WORDS` beats, `tlast` on the last beat
  (`axis_tx_adapter` counts beats and sets a sticky `frame_error` otherwise);
- one `comma` ordered set (`SYNC_PATTERN`, default `0xBC`) precedes the payload
  on the wire;
- each RX frame is exactly `WORDS` payload beats with `tlast` on the last one.

Ethernet frames, AXI bursts and most interconnects are variable length, so
`PKT_ENABLE=1` inserts the adaptation layer the integration has to have:

| stage | module | function |
|---|---|---|
| TX framer | `axis_frame_packetizer` | slice one variable-length frame into `WORDS`-beat fragments, header beat carries `last_frag` + `frag_len` |
| TX ordered set | `axis_ordered_set_insert` | put the `comma` back in front of every fragment chain |
| RX deframer | `axis_frame_depacketizer` | reassemble the fragments, raise `tlast` at the original frame end |

```
s_axis (any length) ─▶ packetizer ─▶ ordered_set_insert ─▶ comma+WORDS ─▶ FIFO ▶ link
link ▶ FIFO ─▶ comma+WORDS ─▶ depacketizer ─▶ m_axis (any length)
```

Details that matter for an integration:

- **`frag_len` is 1..WORDS-1**; the header occupies one slot of the internal
  frame, so `WORDS` must be >= 3 with `PKT_ENABLE=1` (a 1-beat frame has no
  room for a header) and >= 2 without it.
- **Short frames are zero-padded**, not truncated, so a stale bus can never leak
  into a fragment.
- **A zero-length frame** (`tlast` on the first beat) raises the packetizer's
  `frame_error` for that frame and is still emitted as one placeholder payload
  byte, so the link never deadlocks.
- **Fragments of one frame are never interleaved**; two SoC frames always appear
  as two clean fragment chains.
- `pkt_tx_frame_error` and `pkt_rx_frame_error` are the two sticky framing
  errors, both `0` when `PKT_ENABLE=0`.

The ordered set is inserted ahead of the CDC FIFO, in the AXIS clock domain,
because the internal frame shape is fixed at that boundary.  `clear` flushes a
half-inserted comma, so an APB `tx_enable`/`clear` cannot leave a frame
wedged.

## Control plane

| CSR | access | contents |
|---|---|---|
| 0 | RW | `[0]` tx_enable, `[1]` rx_enable, `[2]` rx_flush, `[3]` tx_clear, `[4]` rx_clear, `[5]` clear link-health counters, `[6]` latch cycle counters |
| 7 | RO | link status: busy, frame seen, frame overflow, fifo flags |
| 8 | RW | BIST control: enable/start/clear/inject/loopback/snapshot, word mode |
| 10, 11 | RO | coherent BIST snapshots (error count, bit count) |
| 12 | RO | BIST status: width code, inject, loopback, snapshot valid, running, error |
| 13, 14, 15 | RO | training best_errors, training status, snapshot valid + CRC sticky |
| 16, 17 | RO | `code_error_count`, `link_drop_count` (saturating; clear via CSR0[5]) |
| 18, 19 | RO | measured TX/RX cycles, latched as a matched pair by CSR0[6] |

`CSR7[8]` is the receiver's comma-search watchdog (`comma_lock_stuck`),
`CSR7[9]`/`CSR7[10]` say either counter saturated.  See
`docs/phy_base_layer.md`.

CSR0 is level-sensitive and crosses into `tx_clk`/`rx_clk` through 2FF
synchronizers; the clear/flush bits are turned into single-cycle pulses in
their destination domain.  The telemetry CSRs come across through
`cdc_snapshot`, so a multi-bit field can never be read half-updated.

```bash
# bring the link up
apb_write(0x00, 0x3);        # tx_enable | rx_enable
# a BIST snapshot
apb_write(0x08, 0x01);       # BIST enable
apb_read(0x08, &v); v[4] = snapshot request
apb_read(0x0A, &err); apb_read(0x0B, &bits);
```

## Verification

| test | covers |
|---|---|
| `tb_serdes_soc_cdc_wrapper` | CSR map, CDC, BIST, loopback |
| `tb_soc_cdc_8b10b` | per-frame 8b/10b with disparity re-seed |
| `tb_soc_cdc_packetizer` | `PKT_ENABLE=1`: frame lengths 1, WORDS-1, WORDS, 2*WORDS+3 and 17 round-trip byte-exactly through the serial loopback, `tlast` reconstructed by the deframer, no framing or code error |
| `tb_soc_cdc_training`, `tb_soc_cdc_crc`, `tb_soc_cdc_word_bist` | training FSM, CRC path, word BIST |

`tb_soc_cdc_packetizer` is the one that proves the adaptation layer end to end:
5 frames, 36 bytes, verified on the SoC port after a full serial round trip.

## Limits

- **`interface.framing: variable` is `preset: soc` + `coding: raw` only.**
  `spec/soc_stream.yaml` is the reference and generates the package with
  `PKT_ENABLE=1` plus the `axis_frame_packetizer` /
  `axis_ordered_set_insert` sources (regress case
  `generator_package_soc_stream`: filelist, P1 contracts, iverilog compile and
  sv2v+Yosys all pass).  8b10b is rejected: the codec owns the word cadence and
  the framing layer is not integrated with it yet.
- No CRC/reliable-transport integration at this layer: `axis_reliable_link`
  composes on the SoC side of the port, not inside the wrapper.
- The framing layer adds 1 header beat per `WORDS`-1 payload bytes, so a
  64-byte Ethernet frame at `WORDS=4` costs 25% overhead.  Raise `WORDS`
  (up to 64) to amortise it.
