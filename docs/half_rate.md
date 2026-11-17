# Half-rate rate mode (TX and RX endpoints)

`rate_mode: half` splits the endpoint into two clock domains:

| domain | clock | contents |
|---|---|---|
| bit | `clk_bit` | front sampler, final 2:1 bit mux, CDR sample boundary |
| word | `clk_word = clk_bit / 2` | capture bank, bit/word counters, frame status |

The wide parallel registers never toggle at the line rate.  For a 32 x 8
payload that is 256 flops on the word clock instead of the bit clock.

Both clocks must come from the same source with `clk_word` the divide-by-2
clock of `clk_bit`.  The generated SDC enforces this with
`create_generated_clock -divide_by 2`, and both clocks are declared as
`CLOCK_PORT` entries so CTS buffers the pair.

## TX

`tx.rate_mode: half` selects `serdes_tx_half_rate_digital`
(`serializer_half_rate`).  Capture and word bookkeeping run on `clk_word`; the
final bit selection runs on `clk_bit`.  It is available for `target: tx` only
and requires `interface.enable_mode: static`.

```yaml
target: tx
tx: {architecture: word_streaming, rate_mode: half}
```

## RX

`rx.rate_mode: half` selects `serdes_rx_half_rate_digital`.  The pipeline is:

```
rx_serial_in -- receiver_sampler(clk_bit) --> cdr_data_out
             --> rx_pair_align (2 bits -> 1 pair, clk_word)
             --> deserializer_half_rate (word bank, clk_word)
             --> comma_align (clk_bit) drives frame alignment
```

Handshake details that matter:

- `rx_pair_align` holds the completed pair in dedicated holding registers and
  toggles a sequence bit.  The word domain compares the sequence bit with its
  own copy, so a pair is never lost when alignment shifts the bit phase.  A
  one-cycle pulse crossing into `clk_word` does not work here: after an align
  the pair completion moves by one bit and the pulse can fall outside the word
  window.
- `deserializer_half_rate.align` loads the first pair of a frame directly into
  word0 bits 0-1 instead of appending it.  `comma_align` drops the pattern's
  last bit and restarts the pair phase, so the next pair is word0 bits 0-1.
- `ALIGN_MODE=2` (`align: comma_per_frame`) re-arms after every `rx_par_valid`,
  so each frame realigns on its own sync word.

### Scope of the first integration

`rx.rate_mode: half` currently requires:

- `target: rx` (the half-rate link top is a separate integration),
- `rx.clocking: source_sync` (`clk_bit` is the bit clock),
- `link.coding: raw`,
- `rx.align: external | comma | comma_per_frame`,
- `interface.enable_mode: static`,
- `link.payload_width >= 4`.

Oversampling (`OS >= 2`), bang-bang and multiphase CDRs run on an
`OS x bit-rate` sample clock.  Their parallel bank needs a divided
sample-clock contract (`clk_word = clk_sample / (2*OS)`), which is a separate
follow-up.  Do not pass `rate_mode: half` with those CDRs; the generator
rejects it.

### Evidence

```bash
python3 scripts/gen_ip.py --spec spec/rx_half_rate.yaml --out build/gen/rx_half --force
make build/tb_rx_half_rate build/tb_rx_half_rate_32
./build/tb_rx_half_rate        # 8 x 4, 2 frames
./build/tb_rx_half_rate_32     # 32 x 8, 2 frames
```

`tb_rx_half_rate` sends a comma (0xBC) followed by payload words LSB-first,
checks that `rx_par_data` matches the frame bit order, that `rx_frame_locked`
is high, and that a second frame re-aligns on its own comma.  Both the 8 x 4
and 32 x 8 parameterisations pass.  `verification/tests/test_rate_mode.py`
covers the generator contract (design selection, clock ports, generated clock,
file list) and the rejection cases.

## Not yet done

- Half-rate link top (`serdes_half_rate_digital_top`): needs the TX/RX pair,
  a shared `clk_word`/`clk_bit` contract and the interface/CDC wrappers.
- Oversampling/bang-bang half-rate: divided sample-clock contract.
- Half-rate RX with the DLL phase mux (CDR_MODE=4): the DLL/PPI functional
  gates (low transition density, `phase_offset` 2/3) still gate that work.
