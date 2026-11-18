# Multi-lane links and lane-to-lane deskew

`serdes_multilane_top` bundles several source-synchronous lanes into one word
stream.  A transmitter drives the same word slot on every lane at the same time;
the receiver still sees a per-lane whole-word offset, because each lane has its
own CDR lock time, its own elastic FIFO and its own channel delay.  The deskew
block removes that offset and releases the lanes in lockstep.

```
TX  tx_data[l] ── serializer_stream ── tx_serial_out[l]
                                          │  per-lane delay
RX  rx_serial_in[l] ── serdes_rx_digital ── async_fifo ──┐
                     (CDR_MODE=2, ALIGN_MODE=1)          │ core_clk
                                                  lane_deskew ── rx_data / rx_valid
```

## TX slot schedule

Every lane is fed from one shared schedule and a word is presented on every
cycle while `tx_enable` is high — sync, marker, payload or idle.  Leaving a gap
would stall the serializer mid-word, which shifts the receiver's 8-bit word
counter and desynchronises the marker cadence.

| slot | word | when |
|---|---|---|
| sync | `SYNC_WORD` (default `0xBC`) | once, right after enable |
| marker | `{MARKER_TAG, epoch}` | right after sync, then every `MARKER_PERIOD` accepted payload words |
| payload | `tx_data[l]` | `tx_valid && tx_ready` |
| idle | `{IDLE_TAG, 0}` | no payload available |

`tx_ready` is high only on a payload slot (`~send_sync && ~send_marker &&
&ser_ready`), so the source sees exactly one accept per emitted payload word.

## Ordered sets

Markers and idles are reserved words identified by the top `TAG_WIDTH` bits, so
`DATA_WIDTH` must be greater than `TAG_WIDTH`:

| word | tag | low bits |
|---|---|---|
| marker | `MARKER_TAG` (`0xA`) | epoch counter, wraps |
| idle | `IDLE_TAG` (`0xF`) | unused |
| payload | anything else | user data |

Payload values must avoid the reserved tags.  With `TAG_WIDTH=4` and
`DATA_WIDTH=8` the payload space is 224 codes.

## Deskew

`lane_deskew` gives each lane its own elastic buffer and inspects the head word
of all lanes together.

**Hunt.**  A lane discards words until its first marker; that marker resets its
write pointer and arms the lane.  From then on the lane buffers every following
word, idles included, so all buffers contain the same sequence from the
alignment point.  Once every lane is armed, `in_sync`, and holding the same
epoch, the read pointers are set to the buffer base and the lanes are released
together.  Early lanes have been buffering while they waited, so the skew
becomes buffer fill rather than skew.

**Aligned.**  Each beat looks at all heads:

| head classes | action |
|---|---|
| all payload | forward the beat (`out_valid && out_ready`), `beat_cnt++` |
| all idle | consume, forward nothing, `beat_cnt` unchanged |
| all marker | check epoch continuity (`last+1`), epoch agreement and `beat_cnt == MARKER_PERIOD`; consume and pulse `marker_ok` |
| anything mixed, or a failed marker check | `marker_error`, `realign_count++`, drop `aligned`, restart the hunt |

The output is payload only.  `marker_ok` pulses once per verified marker, i.e.
every `MARKER_PERIOD` output words on a clean link, which downstream logic can
use as a frame boundary.  A re-lock costs at most one marker period of payload:
the failed marker beat and the payload buffered behind it are discarded.

`lane_locked[l]` is `in_sync[l] && (aligned || armed[l])`; `in_sync[l]` is the
per-lane word-framing lock (`rx_frame_locked`) synchronised into `core_clk`.

### Parameter constraints

| constraint | why |
|---|---|
| `MARKER_PERIOD > MAX_SKEW` | otherwise a lane can be a whole marker epoch ahead and the epoch match slips |
| `DESKEW_DEPTH >= 2*MARKER_PERIOD + MAX_SKEW`, power of two | a lane that buffers for a full epoch while the slowest lane catches up must not wrap over unread data |
| `FIFO_DEPTH` power of two | `async_fifo` requirement |
| `DATA_WIDTH > TAG_WIDTH` | tag bits are carved out of the word; the top instantiates a fail-closed stub otherwise |

`MAX_SKEW` is a design declaration, not a measurement: the buffer depth is sized
from it and the deskew does not self-detect a larger skew, it just loses lock
repeatedly (`realign_count` climbing).

## RX framing

Each lane instantiates `serdes_rx_digital` with `CDR_MODE=2` (source-synchronous,
`rx_clk[l]` is the recovered bit clock) and `ALIGN_MODE=1` using `SYNC_WORD` as
the comma pattern.  The pattern establishes the 8-bit word boundary and is
consumed by `comma_align`, so the FIFO only sees whole words starting with the
first word after the sync word.  Deskew markers are independent of this: comma
lock is per-lane bit alignment, the marker is lane-to-lane word alignment.

Recovered words cross into `core_clk` through a per-lane `async_fifo`; the
deskew itself runs entirely in `core_clk`, so no per-lane clock reaches the
combining logic.

## SoC-facing bonding layer

The core works on one `DATA_WIDTH` word per lane per beat — the only shape in
which the lanes stay word-aligned.  A SoC port is one wide AXI4-Stream, so
`axis_lane_bonder` / `axis_lane_debonder` (`rtl/axis_lane_bonder.sv`) are the
width adapter:

| module | direction |
|---|---|
| `axis_lane_bonder` | wide AXIS beat (`LANES*DATA_WIDTH`) -> `LANES` parallel words |
| `axis_lane_debonder` | `LANES` parallel words -> wide AXIS beat |

Both are pure handshake converters (no storage, no reordering); lane 0 sits in
the low bits.  `tlast` is **not** carried: a sideband cannot survive the per-lane
serial link, so framing belongs to an in-band ordered set — the coding layer's
`/T/`, an IEEE all-control block, or the packetizer's fragment header.  Put
`axis_frame_packetizer` / `axis_reliable_link` either side of the bonder when a
framed stream is needed.

The debonder gates `m_axis_tvalid` with `link_ok` and counts suppressed beats
on `underflow`, so a re-locking deskew never hands the SoC a partial stripe
silently: while the link is down the core keeps draining and the beats are
dropped, not stalled.

## Link health

| signal | meaning |
|---|---|
| `lane_locked[l]` | per-lane framing established and participating |
| `lane_fault[l]` | sticky: a lane had framing and lost it (cleared by reset / `rx_flush`) |
| `aligned` | deskew is in the aligned state |
| `link_ok` | `aligned & all lane_locked & no lane_fault` — the gate used by the debonder |
| `marker_ok` / `marker_error` / `realign_count` | marker cadence telemetry |

`lane_fault` is deliberately sticky: a lane that dropped out and recovered
still shows up, so a marginal channel is visible in telemetry instead of
silently costing a marker period every so often.

## CDR mode

Every lane instantiates `serdes_rx_digital` with the top's `CDR_MODE`
parameter:

- `CDR_MODE=2` (source-synchronous) — `rx_clk[l]` is the recovered bit clock,
  one bit per clock, `CDR_BYPASS=1`.
- `CDR_MODE=3` (bang-bang) — `rx_clk[l]` is the oversampling clock,
  `OVERSAMPLE>=4`, `CDR_BYPASS=0`.

The top rejects `CDR_MODE!=2` with `OVERSAMPLE<4` or `CDR_BYPASS=1` at
elaboration: an oversampling loop with a bypassed CDR would sample at the wrong
instant and never lock.  Structurally nothing else changes — each lane still
writes whole words into its own `async_fifo`, and the deskew is mode-agnostic.

Coverage note: the verified multi-lane loopbacks (`tb_multilane_top`,
`tb_multilane_axis`) run `CDR_MODE=2`.  The bang-bang path is exercised
single-channel by `tb_cdr_bang_bang` / `tb_cdr_adaptive`; a closed-loop
multi-lane bang-bang simulation needs the jitter/phase channel model and is not
in this test set, so treat the multi-lane `CDR_MODE=3` configuration as
structurally supported but not yet waveform-verified end to end.

## Top-level interface

| group | signals |
|---|---|
| TX | `tx_clk`, `tx_rst_n`, `tx_enable`, `tx_valid`, `tx_ready`, `tx_data[LANES-1:0][DATA_WIDTH-1:0]`, `tx_serial_out[LANES-1:0]` |
| RX serial | `rx_clk[LANES-1:0]`, `rx_rst_n`, `rx_enable`, `rx_flush`, `rx_serial_in[LANES-1:0]`, `rx_frame_locked[LANES-1:0]` |
| core | `core_clk`, `core_rst_n`, `core_enable`, `rx_data`, `rx_valid`, `rx_ready` |
| status | `aligned`, `marker_ok`, `marker_error`, `realign_count[9:0]`, `lane_locked[LANES-1:0]`, `lane_fault[LANES-1:0]`, `link_ok` |

```bash
make build/tb_lane_deskew build/tb_multilane_top build/tb_multilane_axis
./build/tb_lane_deskew     # 4 lanes, 0/2/5/8 word skew, one marker deleted
./build/tb_multilane_top   # 4-lane serial loopback, 0/1/2/3 word skew
./build/tb_multilane_axis  # bonder -> core -> debonder, wide AXIS both sides
```

`tb_multilane_axis` drives the whole SoC-facing path: 240 wide beats in,
per-lane serial delays of 0/1/2/3 words, 240 wide beats out with byte-exact
comparison (lane order included), `link_ok` high, no `lane_fault`, and a unit
check of the debonder's `link_ok` gating (`underflow` counts the dropped beats
and the core is never stalled).

`tb_lane_deskew` drives 4 lanes with 0/2/5/8-word skew, checks lockstep delivery
and payload ordering, then deletes one marker from lane 2: the next marker beat
is mixed, `marker_error` pulses, exactly one marker period (16 words) is lost,
and delivery resumes in lockstep.  `tb_multilane_top` loops the serial lines back
with 0/1/2/3-word serial delays and checks that all 200 payload words arrive in
order on all lanes.

## Generator preset

`preset: multilane` emits `serdes_multilane_top` (plus `axis_lane_bonder`) as a
package.  `spec/multilane.yaml` is the reference; the spec fields that matter:

```yaml
link:
  payload_width: 8
  words: 1
  lanes: 4              # 2, 4 or 8
  line_rate_mbps: 25
  coding: raw
deskew:
  marker_period: 16     # power of two, must exceed max_skew
  max_skew: 8           # whole words
interface:
  preset: multilane
  data: axi4_stream
  cdc: async_fifo
```

The generator derives `DESKEW_DEPTH` as the next power of two covering
`2*marker_period + max_skew`, sets `LANES`/`MARKER_PERIOD`/`MAX_SKEW`, maps
`rx.architecture` to `CDR_MODE` (`source_sync` -> 2, `bang_bang_oversampling`
-> 3 with `OVERSAMPLE`), and emits three clocks (`tx_clk`, `rx_clk`,
`core_clk`) as asynchronous groups.  The manifest reports
`capabilities.lane_bonding = {selected: 4_lane, lanes: 4, marker_period: 16,
max_skew_words: 8, integrated: true}`.

```bash
python3 scripts/gen_ip.py --spec spec/multilane.yaml --out build/gen/multilane --force
```

## Bandwidth

The bonded link aggregates the per-lane line rate, minus the ordered-set
overhead:

```
goodput = lanes x line_rate x marker_period / (marker_period + 1)
```

At 25 Mb/s per lane with `marker_period = 16`:

| lanes | raw aggregate | goodput (marker overhead) | with 8b/10b (x0.8) |
|---|---|---|---|
| 2 | 50 Mb/s | 47.1 Mb/s | 37.6 Mb/s |
| 4 | 100 Mb/s | 94.1 Mb/s | 75.3 Mb/s |
| 8 | 200 Mb/s | 188.2 Mb/s | 150.6 Mb/s |

The sync word costs one word once per link start, and idle words are only sent
when the source has nothing to send, so they do not reduce goodput.  A larger
`marker_period` trades re-lock latency (up to one period of payload is lost per
re-align) for overhead: at 16 the cost is 5.9%, at 64 it is 1.5%.

## Limits

- The deskew is a pure word-alignment block: it does not do bit de-skew, and it
  cannot correct a lane that inverts or reorders within a marker period.
- Marker and idle words are stolen from the payload space; there is no
  independent control channel, so the coding layer above must not need every
  word code.
- Per-lane FIFO over/underflow is not reported.  Size `FIFO_DEPTH` for the
  `core_clk`/`rx_clk` frequency offset across the whole packet, or add an
  overflow counter at the integration level.

## See also

- `docs/protocol_phy_review.md` — how a production protocol PHY does lane
  deskew (PCIe soft PHY `RXLaneDeskew`): symbol-phase alignment on a protocol
  ordered set (COM in TS1/TS2), per-lane "too far ahead, skip to next COM"
  instead of a global re-hunt, a `stable` output after N clean intervals, and a
  lane mask so the link can run narrower than the design.
- `docs/liteiclink_review.md` — link-level telemetry and watchdog patterns.

Our deskew aligns on a **private** `MARKER_TAG` ordered set, not on a standard
protocol ordered set, and compensates whole words only.  That is appropriate for
a bonded chiplet link but it is why this block cannot be dropped into a
PCIe/USB3/1000BASE-X framing without a shim; the marker cadence assumption the
protocol layer relies on (TX word-aligned, RX position-agnostic) holds here by
construction, and `tb_multilane_axis` is where that invariant is exercised.
- `docs/litepcie_ecp5_review.md` — a production adapter deliberately derives
  "ready" from the **TX** side only, because the receiver losing signal must not
  disable the protocol state machine that is waiting for training sequences.
  Our `link_ok` does follow the RX side; that is correct for a transport-only
  bonded link and must be revisited before a protocol FSM sits on top of it.
