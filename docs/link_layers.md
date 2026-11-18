# Link layers: framing and reliable transport

Two AXIS-side blocks sit between an SoC port and the PCS: a packetizer that makes
variable-length traffic fit the fixed `WORDS`-beat frame handshake, and an
optional reliable-transport pair with sequence numbers and ACK/NAK.  Both are
protocol logic with no line coding; they compose with any of the PCS tops.

```
SoC AXIS ── packetizer ── axis_tx_adapter ── PCS ── ... ── PCS ── axis_rx_adapter ── depacketizer ── SoC AXIS
              │                                                                             │
              └─────────────── axis_reliable_tx / axis_reliable_rx (optional) ─────────────┘
```

## Why a packetizer

`axis_tx_adapter` expects **exactly `WORDS` beats per frame**: it maps beat *i* to
word slot *i*, and the serializer latches the whole frame.  A variable-length
frame either leaves trailing slots stale or arrives long and overruns the slot
budget.  `axis_frame_packetizer` slices one input frame into one or more
fixed-size internal frames instead of refusing it.

### Fragment format

| beat | contents |
|---|---|
| 0 | `{last_frag, 2'b00, frag_len[4:0]}` |
| 1 .. N | payload bytes, zero padded past `frag_len` |

- `frag_len` is 1..`WORDS-1` (the header occupies one slot).
- `WORDS == 1` leaves no room for a header and is rejected at elaboration.
- Fragments of one input frame are emitted back-to-back; two input frames are
  never interleaved.
- `tuser` marks `last_frag`, so the RX side knows when to raise `tlast`.

`axis_frame_depacketizer` is the inverse: strips the header, emits `frag_len`
bytes, raises `tlast` on the final byte of the last fragment, and flags
malformed headers (reserved bits set, `frag_len` out of range, missing `tlast`)
on `frame_error` without emitting them.

The slicer itself can only fail one way: a zero-length input frame (`tlast` on
the first beat).  That raises `frame_error` for the duration of the frame and is
still emitted as one placeholder payload byte so the internal frame contract
holds; the next frame clears the flag.  `tready` backpressure mid-frame is
ordinary AXIS stalling and cannot lose a beat.

`tb_axis_frame_packetizer` sends 5 frames totalling 36 bytes with mixed lengths
(14 fragments), then a zero-length frame and a normal frame, and checks
byte-exact reassembly, the `frame_error` pulse and its clearing (7 frames,
39 bytes, 16 fragments).

## Reliable transport

`axis_reliable_tx` / `axis_reliable_rx` add go-back-N retransmission on top of
`axis_crc_inserter` / `axis_crc_checker`.  The checker runs with
`STORE_AND_FORWARD=1`, so a frame with a bad CRC is dropped before anything
reaches the RX user port.

### Wire format

| beat | contents |
|---|---|
| 0 | header `{padding, seq[SEQ_W-1:0]}` |
| 1 .. N | payload |
| N+1 .. | CRC beats (`CRC_WIDTH`), `tlast` on the last one |

The header is inside the CRC-protected body, so a corrupted sequence number is
rejected like any other corruption instead of being trusted.

### TX behaviour

| parameter | default | effect |
|---|---|---|
| `SEQ_W` | 4 | sequence number width (modulo-`2^SEQ_W` space) |
| `WINDOW` | 4 | frames buffered for retransmission; power of two (the slot index is the low sequence bits) |
| `MAX_FRAME_BEATS` | 64 | per-frame buffer depth |
| `TIMEOUT_CYCLES` | 256 | idle time before a timeout go-back |
| `RETRY_LIMIT` | 8 | restarts before `link_error` latches |
| `CRC_WIDTH` / `CRC_POLY` / `CRC_INIT` | 16 / `0x1021` / all-ones | CRC configuration |

- `ack_seq` is cumulative: `ACK(n)` retires every frame up to and including `n`.
- `nak_seq` restarts the send pointer at `n`.
- A frame that is unacknowledged for `TIMEOUT_CYCLES` while the sender is idle
  restarts from `ack_base` (timeout go-back).
- `retry_count` counts NAK and timeout restarts, and clears on ACK progress;
  it saturates at 255.  `link_error` stays high after `RETRY_LIMIT` restarts
  until reset or `clear`.
- `busy` is high while frames are outstanding.

### RX behaviour

- Every frame is CRC-checked first.  A bad frame is dropped, never forwarded,
  and NAKs the sequence number the receiver expects.
- The in-order frame (`seq == expected`) is forwarded and ACKed.
- A duplicate (`expected - seq <= WINDOW`, i.e. an already-delivered frame) is
  dropped and re-ACKed with `expected-1`.  This is what stops a lost ACK from
  deadlocking the sender.
- Any other non-expected frame is a future frame: dropped and NAKed with the
  expected sequence number.
- `crc_error` and `frame_drop` report the two drop reasons separately.

`tb_axis_reliable_link` runs 9 frames / 34 bytes, drops one ACK, corrupts the
first transmission of frame 7, and checks `retries=2`, `naks=2`, `dups=3` with a
byte-exact delivered stream.

### Sequence-space caution

`WINDOW` must stay well below `2^SEQ_W`.  With `SEQ_W=4` and `WINDOW=4` the
duplicate test `expected - seq <= WINDOW` is unambiguous, but `WINDOW >= 2^SEQ_W/2`
would make old and future frames indistinguishable; keep `WINDOW <= 2^(SEQ_W-2)`
for the go-back-N window to be meaningful.

## Composition

Neither block is a PCS: `tlast`/`tuser` are sideband signals.  On a serial link
the framing layer has to carry the frame boundary in-band — either a fixed frame
length per link, or an explicit end-of-frame ordered set (for example a `/T/`
K-code in 8b/10b, or a terminate control block in 64b/66b).  The multi-lane path
additionally reserves marker and idle words, so payload codes must avoid those
tags (see `multilane.md`).

## Tests

| test | covers |
|---|---|
| `tb_axis_frame_packetizer` | slicing, padding, `frag_len`, reassembly |
| `tb_axis_crc` / `tb_axis_crc_saf` | inserter/checker, store-and-forward drop |
| `tb_axis_reliable_link` | go-back-N, ACK/NAK, duplicate re-ACK, timeout, `link_error` |

## See also

- `docs/liteiclink_review.md` — what a mature FPGA inter-chip link
  (LiteICLink) does that this one does not: link-up watchdogs, coherent clock
  counters, `link_drops`/`invalid_errors` telemetry, no-backpressure RX policy,
  master/slave init asymmetry, XON/XOFF with periodic re-send.
