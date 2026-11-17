# PCS coding layers (8b/10b and 64b/66b)

Two line-coding options sit on top of `serializer`/`deserializer`.  Both keep
the same parallel interface as the raw path (`DATA_WIDTH` bits, `WORDS` per
frame) and add only the coding, the control channel and the lock state machine.

| layer | module | line width | lock source |
|---|---|---|---|
| 8b/10b | `serdes_8b10b_top` (`pcs_8b10b_tx`, `pcs_8b10b_rx`) | 10 bits/symbol | comma (K28.5) |
| 64b/66b | `serdes_64b66b_top` (`pcs_64b66b`, `pcs_64b66b_rx_gearbox`) | 66 bits/block | 2-bit sync header |

## 8b/10b: control symbols

The 8b/10b tops carry the control path next to the data, not inside `tuser`:

| signal | direction | meaning |
|---|---|---|
| `tx_charisk[WORDS-1:0]` | in | one bit per byte: 1 selects the K-code table |
| `rx_charisk[WORDS-1:0]` | out | 1 when the decoded symbol is a control code |
| `rx_code_error` | out | running-disparity or invalid-symbol violation |

`pcs_8b10b_tx` passes `ctrl` straight through, so `/I/` (K28.3), `/S/` (K28.1)
and `/T/` (K28.5) ordered sets are ordinary `tx_charisk` bits.  Nothing in the
TX path constrains which K-codes are used; the framing top decides.

## 8b/10b: comma alignment

`pcs_8b10b_comma` replaces the generic `comma_align` when `ALIGN_MODE=3`.  It
matches the **10-bit** K28.5 in both polarities:

```
K28.5 RD-   10'b0011111010
K28.5 RD+   10'b1100000101
```

so the detector must see the full symbol, not the 7-bit `0011111` substring
that the generic pattern matcher uses.  Alignment fires on the *last* comma bit;
the deserializer then discards that bit and the next serial bit becomes bit 0 of
a fresh 10-bit symbol.

| parameter | default | effect |
|---|---|---|
| `LOCK_COMMAS` | 2 | consecutive commas required before `locked` |
| `RETRIGGER` | 0 | 0: lock once, ignore later commas.  1: every comma pulses `align` |

`RETRIGGER=1` is what a per-frame framer needs: each frame starts with a K28.5,
and the receiver re-derives the symbol boundary (and re-seeds the decoder
disparity) on every frame instead of relying on a one-time lock.  Top level
passes `CORE_ALIGN=0` when `ALIGN_MODE=3`, because the core comma detector would
compete with the 10-bit one.

## 64b/66b: block lock and gearbox

`pcs_64b66b_rx_gearbox` turns the 1-bit RX serial stream into 66-bit blocks:

- a 66-bit sliding window (`LSB_FIRST=0`: sync header is the first bit on the
  wire, matching IEEE 802.3 line order), and
- a sync-header voter: a window position is accepted after `GB_LOCK_BLOCKS`
  valid headers and dropped after `GB_BAD_BLOCKS`, with `GB_IDLE_TIMEOUT`
  releasing the lock when the line goes quiet.

`pcs_64b66b_ctrl_decode` then classifies each block from its sync header and the
8-bit block type field:

| `rx_ctrl_kind` | meaning |
|---|---|
| 0 | pure data (`01` header) |
| 1 | all-control (`10`, type `0x00`) |
| 2 | mixed start (`10`, type `0x78`) |
| 3 | mixed terminate (`10`, type `0xFF`) |

Control fields live in the **descrambled** payload, so the top feeds
`{gb_block[65:64], dec_data}` into the decoder, not the raw block.

The gearbox is a single-PCS-lane feature.  With `RX_GEARBOX=1` the top:

- requires `WORDS == 1` (otherwise the module is replaced by a fail-closed
  stub, so a mis-configured build cannot silently drop blocks),
- switches TX to one continuous encoder block stream, and
- inserts an IEEE all-control idle block (`ctrl=1`, type `0x00`) between frames
  instead of letting the serializer idle, which would otherwise drift the block
  boundary and corrupt every following block.

Configuration surface on `serdes_64b66b_top`:

| parameter | default | effect |
|---|---|---|
| `RX_GEARBOX` | 0 | enable the 1-bit RX + block-lock + decoder path |
| `GB_LOCK_BLOCKS` | 8 | valid headers to acquire lock |
| `GB_BAD_BLOCKS` | 4 | bad headers to lose lock |
| `GB_IDLE_TIMEOUT` | 128 | idle blocks before lock release |

Note the descrambler needs 58 bits to converge, so the first block after lock
can be wrong; `tb_64b66b_rx` counts blocks from the first *valid* one.

## 8b/10b inside the SoC CDC wrapper

`serdes_soc_cdc_wrapper` can put the 8b/10b layer between the AXIS adapters and
the serializer/deserializer (`ENC_8B10B=1`).  In that configuration:

- the AXIS side stays 8-bit (one byte per beat) — the wrapper does not widen the
  user bus,
- the line side becomes 10-bit (`CORE_WIDTH=10`), and
- framing is per AXIS frame: TX forces K28.5 on beat 0, and RX runs
  `pcs_8b10b_comma(RETRIGGER=1)` so every frame re-aligns on its own comma and
  re-seeds the decoder disparity.

Two details matter for a robust build:

1. **Disparity seed only when idle.**  The RX seed is gated with the decoder
   idle flag (`rx_comma_seed = rx_comma_pulse & rx_dec_idle`).  Applying a seed
   mid-frame would corrupt the symbol already in flight.
2. **Bus idle levels produce false commas.**  A static 0 or 1 on the serial line
   can alias K28.5.  The idle-gated seed plus `ALIGN_LOCK_COMMAS` keeps
   `code_error` from latching on framing noise before the first real frame;
   `tb_8b10b_kcodes` deliberately shows `code_errs=3` from pre-alignment garbage.

`pcs_8b10b_rx_stream` decodes a `SYMBOLS`-wide block and, because the trailing
symbol of a block is the next frame's comma, applies `DROP_LAST` and emits only
payload symbols.  Its state machine has an 8-cycle `S_WAIT` timeout so a missing
comma cannot hang the block pipeline.

## Tests

| test | covers |
|---|---|
| `tb_8b10b` | encoder/decoder tables, all data bytes |
| `tb_8b10b_kcodes` | `/I/ /S/ /T/` end-to-end with `tx_charisk` |
| `tb_pcs_8b10b` / `tb_pcs_comma_lock` | 10-bit comma polarity and lock |
| `tb_pcs_8b10b_realign` | slip + re-align on a K28.5 |
| `tb_pcs_serial_e2e` | 8b10b PCS over `channel_model` |
| `tb_soc_cdc_8b10b` | per-frame 8b/10b in the SoC CDC wrapper |
| `tb_64b66b` / `tb_64b66b_gearbox` | 64b/66b encode/decode and gearbox |
| `tb_64b66b_rx` | sliding-window lock + control-block classification |
| `tb_64b66b_top` | top-level TX/RX with `RX_GEARBOX=1` |

## Limits

- `RX_GEARBOX=1` is single-lane (`WORDS == 1`).  Multi-lane 64b/66b needs one
  gearbox per lane plus a distributor, which is not implemented.
- `pcs_64b66b_ctrl_decode` classifies blocks; it does not extract the 8 control
  bytes of a mixed block into a separate FIFO.
- 8b/10b in the SoC wrapper is byte-wide on the user side.  Wider user widths
  would need the adapters re-parameterised, which is a separate change.
