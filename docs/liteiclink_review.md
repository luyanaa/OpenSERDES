# LiteICLink borrowings: a design review

Reviewed: <https://github.com/enjoy-digital/liteiclink> (BSD-2-Clause, EnjoyDigital).
Files read: `README.md`, `doc/serdes.md`, `doc/serwb.md`,
`liteiclink/serdes/{common,clock_aligner,gtp_7series,gtx_7series,serdes_ecp5}.py`,
`liteiclink/serwb/{core,genphy,datapath,packet,scrambler}.py`.

LiteICLink is not a competitor to this project — it wraps *vendor hard macros*
(Xilinx GTP/GTX/GTH/GTY, ECP5 DCU, Gowin GTR12) while OpenSERDES builds the
digital PCS from scratch for an ASIC flow.  That difference is exactly why it is
useful: it has solved the **board-level and system-level** problems that a
from-scratch PHY also meets eventually, and its choices are battle-tested on
real hardware.

## What they do that we already do (validated, no action)

| LiteICLink | OpenSERDES equivalent |
|---|---|
| 8b10b encode/decode with K characters (`litex.soc.cores.code_8b10b`) | `encoder_8b10b` / `decoder_8b10b`, `pcs_8b10b_*`, `tx_charisk` end to end |
| 66b/64b gearbox, raw PCS passthrough (GTR12) | `pcs_64b66b`, `pcs_64b66b_rx_gearbox`, `serdes_raw_pcs_top` |
| PRBS7/15/31 TX/RX with error counters | `prbs_gen`/`prbs_check`/`prbs_word_*`, BIST CSRs 8/10/11/12 |
| PLL divider search with a hard `ValueError` | `scripts/gen_ip.py` capability gates + measured-PPA selection |
| Static TX/RX polarity invert | `tx_polarity`/`rx_polarity`, `INVERT` in `channel_model` |
| TX inhibit (electrical idle) | `tx_enable` / power-down gating in the SoC wrapper |
| Loopback control (near-end PCS/PMA, far-end) | `LOOPBACK` in `channel_model`, BIST loopback CSR bit |
| CSRs for enable/ready/error/PRBS | CSR0/7/8/10-15 in `serdes_soc_cdc_wrapper` |

Their clocking note ("user clocks must be constrained, false-path the rest") is
what we do through `create_clock` + asynchronous `set_clock_groups` in the
generated SDC.

## What is genuinely worth borrowing

Ordered by expected value for this project, not by how clever it is.

### 1. Watchdog on every link-up FSM (highest value)

`*_init.py`: "A watchdog restarts the sequence when it doesn't complete
(1 ms on 7-Series, 10 ms on UltraScale, 4 ms on GTP RX)".  A transceiver reset
sequence that stalls leaves the link dead with no telemetry.

Our equivalents have **no timeout**:
`link_training_fsm` scans until it finds a lock, `lane_deskew` hunts until all
lanes arm, `pcs_8b10b_comma` searches for a comma, `axis_frame_depacketizer`
waits for the rest of a fragment chain.  Today none of them can distinguish
"still trying" from "structurally stuck".

Borrow: a `*_TIMEOUT_CYCLES` parameter plus a sticky `timeout` output on each of
those four.  This is small RTL and it is the difference between a link that
recovers and a link that hangs.

### 2. Latch counters instead of reading a free-running counter

`serwb/common.py::ClockCycles` — a `GrayCounter` in the source domain, a
`MultiReg` into `sys`, then binary re-decoding bit by bit.  `clock_latch` is a
CSR write that snapshots both TX and RX counts so the two can be *compared*.

Their unit tests rely on this to measure linerate on hardware.  Our
`rx_freq_trim` / `train_best_errors` are synchronized per-bit today, but there is
no coherent multi-bit snapshot of *counters*, and no way to read TX and RX
cycles as a matched pair (which is how you detect a ppm offset on a real board).

Borrow: `rtl/clock_cycles.sv` (Gray counter + Gray→binary sync) and a CSR pair
`clock_latch`/`clock_tx_cycles`/`clock_rx_cycles` in the SoC wrapper.  This also
gives the phase model from `docs/phase_model.md` a measured counterpart: the
model predicts ppm, the CSR measures it.

### 3. `link_drops` and `invalid_errors` as first-class CSRs

`SerdesControl` counts two things we do not:
`invalid_errors` (invalid 8b10b words since ready) and `link_drops`
(ready → not-ready transitions).

We have `code_error` (sticky) and `lane_fault` (sticky per lane) but **no
counters**, so a marginal channel looks identical to a dead one.  Their
`with_auto_retrain` is just a comparator on `invalid_errors` over a window —
which is exactly the pattern our `link_training_fsm` already uses for
`retrain`, so the counter alone would let us reuse it.

Borrow: 32-bit saturating counters `code_error_count` and `link_drop_count`,
plus (optionally) `AUTO_RETRAIN` wired to the existing
`RETRAIN_ERR_THRESH`/`RETRAIN_WINDOW`.

### 4. Do not backpressure the RX link; drop whole words

`serwb/datapath.py::FlowControl`: "No backpressure on the link: received words
are dropped (whole words) when source is not ready, stalling the RX datapath
would lose bits and the word alignment."

We already drop whole beats on overflow in `axis_rx_adapter`
(`frame_overflow`), and `axis_lane_debonder` drops when `link_ok` is low — but
our **packetizer** does the opposite: it stalls `s_axis_tready` mid-frame, which
on a serial link means the framer holds a partial frame indefinitely.  That is
fine behind an async FIFO, and *not* fine if the framing layer is ever moved to
the line side.  Worth an explicit note in `docs/soc_streaming.md` (and the
design stays as-is because our framer sits behind the FIFO).

### 5. Master/slave link-init asymmetry

`_SerdesInit`: the master sends IDLE zeros first (which *resets* the slave),
then K28.5 commas; the slave aligns on those, then sends its own commas until
the master stops seeing them.  Reset ownership is part of the protocol, not a
separate wire.

Our `serdes_multilane_top` sends a `SYNC_WORD` then markers unconditionally and
has no notion of a peer.  For a chiplet/board link this asymmetry is what makes
"which side resets whom" unambiguous — worth keeping in mind when the
multi-lane top gets a peer, though it is a protocol decision rather than a bug
today.

### 6. Comma-based **fixed RX latency** via transceiver restarts

`BruteforceClockAligner` restarts the transceiver until the comma lands on the
LSB of RXDATA, with an honest note that the vendor's `RXSLIDE_MODE=PMA` is
unusable (2 UI jumps every second pulse, incompatible with bypassed buffers).

We align once (`pcs_8b10b_comma`) and accept whatever multi-cycle latency the
elastic buffer adds; our `lane_deskew` then removes lane-to-lane skew *per
marker period*.  For an ASIC that is the right trade (we cannot "restart the
transceiver"), but the note is valuable: **do not claim deterministic latency**
in the multi-lane path, and say why.  Already implicit in
`docs/multilane.md`; worth making explicit.

### 7. Ones-count invalid-symbol check

`RXDatapath`: a received 10-bit symbol with a popcount outside {4,5,6} cannot be
valid 8b10b, checked *before* the table lookup, at ~10 LUTs.

Our `decoder_8b10b` finds the same errors through a 1024-entry LUT
(`o_code_err`), which is correct but costs a full table read on the critical
path and gives no cheap early signal.  A parallel popcount is a cheaper
`code_error` fast path that can gate the elastic buffer write — worth
considering if the decoder ever sits on a timing-critical path.

### 8. Scrambler with periodic K29.7 sync insertion

`serwb/scrambler.py` is a 23-bit LFSR with taps [17,22] and **inserts a K29.7
SYNC every 2^10 cycles** so the descrambler can re-seed without waiting for the
data to converge.

We only have the 64b/66b self-synchronous scrambler (`pcs_64b66b`), which
reconverges in 58 bits by construction.  For a *raw* link a fabric scrambler
plus an in-band sync has a different trade (no 66-bit framing, but an ordered
set every 1024 words) — an option to note in `docs/raw_pcs.md`, not to build
today.

### 9. XON/XOFF link-level flow control using K codes

`FlowControl`: K28.4 (XON) / K28.3 (XOFF) preempt data for exactly one word,
and are periodically re-sent to recover from a lost character; K28.1 fills the
idle stream so the receiver's word alignment never sees line silence.

We have `axis_reliable_link` (go-back-N + ACK/NAK) at the packet layer and
`axis_ordered_set_insert` for the comma.  A word-level XON/XOFF with
**periodic re-send** is a much lighter option when the peer cannot afford
retransmission — and K28.1-as-fill is the complement of the "never gap the
serial line" rule our multi-lane TX already follows.

## What we should not copy

- **Vendor hard-macro wrapping.**  `gtp_7series.py` is 59 kB of primitive
  parameter dictionaries.  Our whole premise is that the PCS is our RTL; the
  equivalent work for us is PDK-specific analog macros, not transceiver config.
- **Migen/LiteX as the build system.**  Our spec → package → LibreLane flow with
  measured-PPA gates is more rigorous for an ASIC signoff; LiteICLink optimises
  for FPGA bring-up speed.
- **`SEMI`-precision assertions on hardware.**  Their PRBS test runs on a board
  for 60 s; our regression is simulation-only and says so.  Do not let a
  borrowed "measured BER" claim leak into our manifest.
- **`with_flow_control` by default.**  XON/XOFF changes the wire protocol; it is
  opt-in there and should be opt-in here.

## Concrete follow-ups this review creates

| # | item | size | why |
|---|---|---|---|
| 1 | `*_TIMEOUT_CYCLES` + sticky `timeout` on `link_training_fsm`, `lane_deskew`, `pcs_8b10b_comma`, `axis_frame_depacketizer` | small | a stalled link must be distinguishable from a slow one |
| 2 | `rtl/clock_cycles.sv` + `clock_latch`/`clock_tx_cycles`/`clock_rx_cycles` CSRs | small | measures the ppm the phase model predicts |
| 3 | `code_error_count` and `link_drop_count` saturating counters | small | a marginal channel must be visible |
| 4 | `AUTO_RETRAIN` on the multi-lane top wired to `link_drop_count` | small | reuse the existing retrain comparator |
| 5 | popcount fast-path `code_error` before the 8b10b LUT | tiny | cheaper early signal, table read off the critical path |
| 6 | document "no deterministic latency" in `docs/multilane.md` and the XON/XOFF + sync-scrambler options in `docs/raw_pcs.md` | docs | do not overclaim |

Items 1–4 compose into one "link health" pass; 5–6 are independent.

## Companion review

`docs/protocol_phy_review.md` covers the protocol layer built on top of these
transceivers (LitePCIe soft PHY, USB3-PIPE, LiteEth basex).  It is the more
demanding comparison: PCIe's `RXLaneDeskew` is a better-specified version of our
`lane_deskew` (symbol-phase alignment, per-lane skip instead of global re-hunt,
published lock quality, lane masking), USB3 contributes in-band error
substitution and electrical-idle debounce, and 1000BASE-X contributes
disparity-neutral idles and a byte-enable realigner.
