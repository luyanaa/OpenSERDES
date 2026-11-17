# Raw PCS mode (`preset: raw_pcs`)

`preset: raw_pcs` generates `serdes_raw_pcs_top`: a flat word bus plus a word
clock, the way an FPGA GT Raw PCS is used.  It replaces the
`WORDS x DATA_WIDTH` frame handshake with one `DATA_WIDTH`-bit word per word
clock.

| signal | domain | meaning |
|---|---|---|
| `tx_data[DATA_WIDTH-1:0]`, `tx_valid` | `tx_usr_clk` | one word per word clock |
| `tx_ready` | `tx_usr_clk` | high when the serializer can take a word |
| `tx_serial_out`, `tx_serial_valid` | `clk` | line-side serial |
| `rx_data[DATA_WIDTH-1:0]`, `rx_valid` | `rx_rec_clk` | one recovered word per word clock |
| `rx_slide` | `clk` | one-cycle bitslip (resets the bit boundary) |
| `rx_align_bypass` | `clk` | mask the internal comma detector |
| `rx_frame_locked`, `rx_lock`, `rx_busy` | `clk` | status |

`tx_usr_clk = rx_rec_clk = clk / DATA_WIDTH`.  The divider is a standard-cell
register, and `gen_ip.py` emits
`create_generated_clock -divide_by $WORD_DIV` for both outputs in all three SDC
stages.  The design stays in the `clk` domain; the word clocks are outputs for
the MAC side, not a second internal domain.

Widths: **16, 20, 32, 40, 64**.  `link.words` must be 1 and `link.coding` must
be `raw`.

## TX continuity

`serializer_stream` with `WORDS=1` accepts the next word on the cycle that
emits the last bit, so `serial_valid` has no bubble between words
(`tb_serializer_stream_cont`: 4 words, 0 gaps).

## Alignment

The internal `comma_align` runs on the recovered serial stream.  Set
`rx.sync_len == link.payload_width` when the comma is a full word (the
generator enforces this for `align: comma`).  `rx_align_bypass=1` disables the
internal detector; an external PCS state machine then owns alignment and uses
`rx_slide` to move the bit boundary by one bit.

## Example

```bash
python3 scripts/gen_ip.py --spec spec/raw_pcs.yaml --out build/gen/raw_pcs --force
make build/tb_raw_pcs build/tb_raw_pcs_32
./build/tb_raw_pcs        # 16-bit, comma + slide + word clock
./build/tb_raw_pcs_32     # 32-bit
```

`tb_raw_pcs` loops the serial output back, sends a comma word plus payload,
checks the recovered words and `rx_frame_locked`, pulses `rx_slide` and checks
that a fresh comma re-aligns, and measures `tx_usr_clk = clk / DATA_WIDTH`.
The same test passes at 16/20/32/40/64 bits.

## Limits

- Endpoint only (`target: rx` or `tx`).  A half-rate/Raw PCS link top is a
  separate integration.
- Raw coding: no 8b10b, no scrambler.  Those belong to `pipe_lite` or the
  coding tops.
- Digital only: no PMA, no analog sampler, no electrical idle.

## See also

- `docs/pipe_lite.md` — PIPE-Lite shell with 8b/10b and K-code control.
- `docs/pcs_coding.md` — the 8b/10b and 64b/66b coding layers.
- `docs/link_layers.md` — variable-length framing and reliable transport.
- `docs/multilane.md` — multi-lane links and lane-to-lane deskew.
