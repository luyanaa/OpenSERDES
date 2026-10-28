# Timing optimization: structure before faster flops

This note records the recommended order for the OpenSERDES digital TX/RX path.
It is deliberately separate from the standard-cell survey: a faster library or
a custom flop can hide a structural problem, but it cannot remove the fanout,
clock load and long combinational chains that dominate the current serializer
and deserializer.

## 1. Current structure and bottleneck

`rtl/serializer_stream.sv` captures `WORDS` words, then serializes one bit per
`clk`.  `rtl/deserializer.sv` shifts one bit per `clk` into a one-hot selected
word/bank.  The one-hot capture was a correctness improvement, but it leaves:

- one clock per serial bit, so the final mux/register and clock tree run at the
  full line rate;
- a wide input bank and a wide word-select network;
- a large enable/clock fanout at the capture and output stages;
- little opportunity to retime parallel logic at a lower rate.

`docs/clocking.md` already shows why simply adding buffers is not a fix: the
DFF count and fanout per leaf determine skew, and a 266-DFF serializer needs a
different tree than the original 13-DFF block.

## 2. Candidate architectures

| Architecture | What runs at full rate | What runs at half/quarter rate | Risk | Priority |
|---|---|---|---|---|
| Current one-hot capture | final bit mux, capture clock | none | baseline, known correctness | keep |
| 2:1 hierarchical MUX TX | final 2:1 mux + output register | word capture/serializer bank | clock/phase generation, rate-domain handoff | **first** |
| Half-rate TX/RX | DDR/2:1 final mux; edge sampling | parallel word path | two-phase clock, RX edge alignment, CDC | high |
| Quarter-rate TX/RX | 4:1 final mux / 4-phase sampler | most parallel logic | 4-phase skew, phase calibration, testbench complexity | medium-high |
| Multi-phase clock + custom final stage | small custom mux/SA-FF | all parallel logic | analog/custom timing, DFT | medium |
| Custom SA-FF/TSPC sampler | only the sampler | no structural change | PDK/analog work, risk | late |

Tree-type and shift-register are not mutually exclusive.  A hierarchical mux,
a shift register, a divider and pipeline stages can be combined; the label
should describe the clocking boundary, not a single RTL coding style.

### 2.1 2:1 hierarchical MUX TX

The first structural step should keep the existing word capture and add a
small final stage:

1. Capture `WORDS` words at the word clock.
2. Present two adjacent bits to a 2:1 mux at a 2x bit clock (or use both edges
   of the bit clock).
3. Register only the final mux/output flop at the full bit rate.

The parallel capture and word selection then have a full word period, while
only a two-input mux and one output register see the UI period.  This is the
smallest change with a measurable clock-load benefit and a tractable
verification surface.

### 2.2 Half-rate TX/RX

Half-rate does **not** make the whole PHY half-rate: the pad still changes once
per UI and the RX sampler still observes every UI.  It moves the serializer
final mux and the deserializer first stage to a 2-phase/DDR structure, while
the word bank and alignment logic can run at half rate.  Required additions:

- a bit-clock/2 and a 90-degree phase relationship (or DDR output cells);
- an explicit rate-domain handoff with reset and flush behavior;
- a scoreboard that checks even/odd bit order and frame boundaries;
- RX edge/center sampling and comma alignment re-verification at both phases.

### 2.3 Quarter-rate and multi-phase

Quarter-rate only pays off after the 2:1/half-rate path and clock plan are
proven.  Four-phase skew, phase calibration and per-phase duty-cycle error
become the limiting factors; a custom SA-FF or TSPC sampler is a later step,
not a prerequisite.

## 3. Proposed generator interface

Add a rate-mode field rather than silently changing the baseline:

```yaml
tx:
  architecture: word_streaming
  rate_mode: full | mux2 | half | quarter   # default: full
rx:
  architecture: source_sync
  rate_mode: full | half | quarter          # default: full
```

The generator must record the selected mode in the manifest and pair it with a
matching testbench/profile.  A mode must not be declared available until its
scoreboard, reset/flush and clock-domain tests pass.

## 4. Verification plan

1. **2:1 mux model**: bit-exact loopback against `serializer_stream` for all
   `LSB_FIRST`, `WORDS`, `DATA_WIDTH` combinations; back-to-back frames.
2. **Half-rate**: even/odd bit order, frame boundary, PPM tracking and
   reset/flush at phase boundaries; compare eye/PPA against full-rate.
3. **Quarter-rate**: per-phase skew injection and lock/slip scoreboard.
4. **Library/PVT**: run the same RTL through the selected library, then the
   existing PVT/MC and JTOL evidence levels; do not use a custom flop to mask
   a failing standard-cell path.

## 4.1 First prototype result

`rtl/serializer_hier_2to1.sv` is bit-exact against `serializer_stream` for
8x4, 32x8, 5x3, LSB/MSB and randomized backpressure (200 frames).  The first
Yosys/ABC mapping to sky130 `hd` at a 5 ns clock gives the same worst path
slack for both variants (0.406 ns, TT, 32x8):

```bash
make timing-compare TIMING_LIB=/path/to/sky130_fd_sc_hd__tt_025C_1v80.lib
```

The result is important: a purely combinational 2:1 mux tree is logically
equivalent to the flat mux and ABC normalizes both to the same mapped network;
it does **not** by itself shorten the critical path.  The prototype is a
correctness/reference step.  A measurable gain requires one of:

- registering the mux-tree levels with a pipeline/retiming contract;
- a half-rate/DDR final stage with a real clock-phase plan;
- attacking the capture/control fanout that dominates the current path.

Do not claim a timing optimization from the mux-tree prototype alone.

## 4.2 Half-rate prototype result

`rtl/serializer_half_rate.sv` adds a true two-clock prototype:

- `clk_word` captures `WORDS` words and owns the input handshake;
- `clk_bit = 2 * clk_word` emits one bit per bit clock;
- a level request/acknowledge crosses the two domains; `data_q` is stable
  before the bit domain starts;
- the serial stream is bit-exact against `serializer_stream` for 8x4, 16x4,
  32x8 and 5x3, LSB/MSB, 40 frames.

sky130 HD TT mapping at a 5 ns bit clock (10 ns word clock) gives:

| Variant | Worst register-to-register slack |
|---|---:|
| `serializer_stream` (full rate) | 0.406 ns |
| `serializer_hier_2to1` (mux tree only) | 0.406 ns |
| `serializer_half_rate` (word + 2x bit) | **0.658 ns** |

The half-rate prototype gains about **0.25 ns** at this point.  This is a
structural gain, not a library swap.  It is still a prototype: the clock-phase
contract, reset ordering, `enable` semantics, SoC/generator wiring, and the
full RX half-rate scoreboard remain to be integrated before a rate-mode claim.

## 5. Recommendation

1. Keep full-rate one-hot as the correctness baseline.
2. Implement **2:1 hierarchical MUX TX** first, with an optional generator
   field and a bit-exact scoreboard.
3. Integrate the half-rate TX prototype into the generator (`rate_mode`),
   then add the matching RX half-rate sampler/scoreboard; only after that
   evaluate quarter-rate.
4. Treat a custom SA-FF/TSPC sampler as a late, separate analog/custom block.
5. Re-run standard-cell mapping (`dfflibmap`/ABC) and DFF/NAND cell checks on
   every candidate library; structure and library choice must be evaluated
   together, not one after the other.
