# Pre-PnR STA optimization

## 1. The first optimization is STA methodology, not RTL

The first pre-PnR runs were dominated by the asynchronous reset input, not by
the 256:1 serializer mux.  `ready = ~active_q & rst_n` made `rst_n` fan out to
267 loads, and the generic SDC applied a 4–20 ns input delay to it.

Representative effect (data-path WNS at ss):

| Block | PDK | Period | Raw WNS | WNS after reset exclusion |
|---|---|---:|---:|---:|
| Serializer | sky130 | 20 ns | −6.24 ns | **+4.44 ns** |
| Serializer | gf180mcuD | 100 ns | −13.74 ns | **+39.70 ns** |
| Serializer | IHP SG13G2 | 20 ns | +5.66 ns | **+12.02 ns** |
| Serializer | icsprout55 H7CL | 20 ns | −1.01 ns | **+7.94 ns** |

Rules:

- Async reset is **not** a data input.  Exclude `rst_n` and the flop async pins
  from data-path STA pre-CTS.
- Analyze reset recovery/removal separately after the reset tree is inserted
  and buffered.  Do not silently waive it at signoff.
- Static enables (`tx_enable`, `rx_enable`) should be `set_case_analysis` or
  false-path for pre-PnR; they are not data toggles.
- Pulsed controls (`load`, `flush`, `align`) are real input paths; register
  them at the block boundary if they become critical.
- Use one block SDC per leaf block (`block_serializer.sdc`,
  `block_deserializer.sdc`) and a separate top SDC.

## 2. Data-path results after reset exclusion

Bracketed by tested periods; not an exact fmax claim.

| PDK / class | Block | Passing period | Failing period |
|---|---|---:|---:|
| sky130 (130 nm) | Serializer | 20 ns | 10 ns |
| sky130 (130 nm) | Deserializer | 10 ns | — |
| IHP SG13G2 (130 nm) | Serializer | 10 ns | — |
| IHP SG13G2 (130 nm) | Deserializer | 10 ns | — |
| icsprout55 H7CL (55 nm) | Serializer | 20 ns | 10 ns |
| icsprout55 H7CL (55 nm) | Deserializer | 10 ns | — |
| gf180mcuD 5 V (180 nm) | Serializer | 100 ns | 40 ns |
| gf180mcuD 5 V (180 nm) | Deserializer | 40 ns | — |

Interpretation:

- **Deserializer is not the bottleneck** in this RTL; the generated word
  register bank is fast enough at these periods.
- The measured serializer worst path is **not** the 256:1 data mux.  It is the
  unbuffered load-enable/control net:

```text
busy -> ready -> NAND/AND (fanout ~257) -> 256 input-register enables -> ...
```

  On sky130 at 10 ns the load-enable net alone contributes ~9.5 ns; the mux2
  data path is only ~4.6 ns.  This is a pre-PnR buffering artifact, not a
  reason to pipeline the data mux first.
- 55 nm is not automatically faster than 130 nm for this architecture; the
  256:1 mux and wire load dominate, and H7CL only closes at 20 ns in this
  test.
- GF180 5 V cells are much slower: with one-hot capture/select, the serializer
  closes at 20 ns (+2.50 ns) and the deserializer at 20 ns (+8.14 ns).  A full
  20 ns link with `OVERSAMPLE=2` still fails because the RX side becomes 10 ns;
  40 ns TX / 20 ns RX closes setup (+2.87 ns) with one hold violation.  Treat
  GF180 5 V as a slow-5 V proxy, not as a 0.35 µm device.

## 3. Technology-agnostic RTL optimizations

### 3.1 Buffer/register the load-enable first

The 256-bit input register bank needs one enable per flop.  A 257-load control
net is not a signoff-representative pre-PnR result.  Options:

- run `repair_design`/buffer insertion with a realistic `set_max_fanout`
  before trusting the WNS;
- capture the input in 8 word groups over 8 cycles (one 32-bit word per cycle),
  reducing the instantaneous enable fanout to ~32;
- use a streaming input (no 256-bit input register) when the external source
  can hold the frame;
- only then consider pipelining the 256:1 data mux.

Do not split the capture into per-word `always_ff` blocks and assume synthesis
will keep separate enables; in the experiment it merged them into a single
258-load AND and made WNS worse.

Measured effect of the word-group capture (`CAPTURE_MODE=1`):

| PDK | Period | Single-cycle capture | Word-group capture |
|---|---:|---:|---:|
| sky130 | 10 ns | −5.56 ns | **+2.78 ns** |
| IHP SG13G2 | 10 ns | +1.04 ns | **+5.66 ns** |
| icsprout55 H7CL | 10 ns | −2.19 ns | **+7.21 ns** |
| icsprout55 H7CR | 10 ns | — | **+6.39 ns** |
| icsprout55 H7CH | 20 ns | — | **+13.15 ns** |
| gf180mcuD 5 V | 40 ns | −19.88 ns | **+22.50 ns** |

The cost is latency: `ready` is low for `WORDS` capture cycles, and the
external parallel source must hold the frame stable during those cycles.
The final implementation uses a **one-hot capture select**, not a binary word
counter: each select bit drives only DATA_WIDTH flops.  A binary counter made
one bit fan out to 103 loads and left 8 failing paths on sky130 at 10 ns.

### 3.2 Pipeline the serializer data path

Split the 256:1 mux into two stages:

```text
Stage A:  data_q -> bit-select (8 word bits) -> register word_bit_q/word_sel_q
Stage B:  word_bit_q -> word mux -> serial_out register
```

- Breaks the data-to-output path roughly in half.
- Adds 1–2 cycles latency; `serial_valid`, `frame_done`, and `ready` must be
  delayed accordingly.
- Recommended default: 1 stage for 130–180 nm, 2 stages for 0.35 µm+ or
  55 nm when targeting >100 MHz.

### 3.3 Deserializer: avoid the indexed-write mux

The current implementation writes `word_data[word_sel][bit_sel]`, which
synthesizes a 256-bit write mux.  Alternatives:

- shift register with output bit re-mapping;
- per-word shift enable (one hot word select);
- dual-bank word register with a registered word boundary.

The current deserializer passes at 10 ns on 130/55 nm, so this is lower
priority than serializer pipelining.

### 3.4 Register control and wide buses

- Register `lock`, `enable`, `flush`, and `align` at the block boundary; do not
  let a decoded control net fan out to 256 flops combinationally.
- Partition the 256-bit bus into 8 × 32-bit local groups with per-word
  registers.  Do not route a single 256-bit net across the block.
- Keep leaf fanout 8–10 (130/180 nm), 10–16 (55 nm), 4–6 (0.35 µm+).
- Do not add pipeline depth blindly; balance insertion delay, power, and area.

## 3.5 Top-level clock ratio: oversampling CDR multiplies the rx clock

The top-level `serdes_digital_top` uses `rx_clk = OVERSAMPLE × tx_clk`.  With
`OVERSAMPLE=4`, a 20 ns TX period means a 5 ns RX period.  Capture-mode top
results at 20 ns TX:

| PDK | TX period | RX period | Setup WNS (ss) |
|---|---:|---:|---:|
| sky130 | 20 ns | 5 ns | −0.32 ns (44 paths) |
| icsprout55 H7CL | 20 ns | 5 ns | +2.57 ns |
| IHP SG13G2 | 20 ns | 5 ns | +1.78 ns |

With `OVERSAMPLE=2`, sky130 top at 20 ns TX / 10 ns RX closes:
**+4.59 ns setup (ss)**.  The pre-PnR estimate initially reported about 20
max-cap and >3000 max-slew violations.  `repair_design` inserted 558 buffers
and drove max-cap violations to **0** (TT corner); max-slew fell from 3217 to
2186 post-CTS.  After routing, setup is **+5.05 ns**, hold **+0.072 ns**, and
max-cap/max-fanout are **0**.  However, 2616 max-slew violations remain on
high-fanout nets (limit 0.2 ns, some nets ~1.1 ns).  Max-cap is clearly
repairable; max-slew is not fully closed and needs post-route ECO/buffer
sizing or a relaxed transition target.

The sky130 limit at 4× is therefore **not** the serializer; it is the 200 MHz
deserializer/CDR sampling domain.  For higher TX rates, either:

- reduce `OVERSAMPLE` to 2, or use source-synchronous/forwarded clock;
- replace the binary word/bit select with one-hot allows;
- use a faster process class (55 nm/IHP).

At 130 nm a 400 MHz RX clock (100 MHz TX with 4× oversampling) is not a
realistic target for this RTL.  At 55 nm, both 200 MHz and 400 MHz RX are now
demonstrated: the 2.5 ns RX wrapper closes at +0.315 ns with one-hot select.

## 3.6 Cross-process endpoints

The digital top is now split into independently synthesizable wrappers:

- `rtl/serdes_tx_digital.sv` — serializer + reset sync.
- `rtl/serdes_rx_digital.sv` — CDR + deserializer + reset sync.

The same RTL/protocol can be synthesized on different PDKs for TX and RX:

| Endpoint | PDK | Period | Setup WNS (ss) |
|---|---|---:|---:|
| TX wrapper | icsprout55 H7CL | 5 ns | +1.03 ns |
| RX wrapper | icsprout55 H7CL, OS=2 | 5 ns | +1.97 ns |
| RX wrapper | icsprout55 H7CL, OS=2 | 2.5 ns | **+0.315 ns** |
| TX wrapper | sky130 | 12 ns | **+0.02 ns** |
| TX wrapper | sky130 | 10 ns | −1.98 ns (10 paths) |
| TX serializer alone | sky130 | 10 ns | +2.78 ns |

The sky130 TX wrapper is slower than the bare serializer because of the reset
synchronizer and wrapper-level control paths; 12 ns is marginal (+0.02 ns),
13 ns is the practical starting point.

Cross-process link evidence:

- 100 MHz link: icsprout55 H7CL top at 10 ns TX with `OVERSAMPLE=2` (5 ns RX)
  closes at **+2.82 ns (ss)**.
- 200 MHz link: icsprout55 H7CL top at 5 ns TX with `OVERSAMPLE=2` (2.5 ns RX)
  closes at **+0.315 ns (ss)**.  This uses the one-hot word/bit select below;
  the binary select previously failed at −0.51 ns.
- Static `tx_enable`/`rx_enable` and async reset/set pins are now excluded from
  data-path STA; they are static/async controls, not data toggles.

The 400 MHz RX endpoint itself is +0.315 ns.  Its only remaining pre-exception
violations were reset recovery into high-fanout `SN`/`SETN` pins; adding these
to the asynchronous exception list removed them.

### 3.7 GF180MCU 5 V at 20 ns

The one-hot deserializer also changes the GF180 5 V result:

| Block | Period | Setup WNS (ss) | Result |
|---|---:|---:|---|
| serializer, capture mode | 20 ns | **+2.50 ns** | 50 MHz feasible |
| deserializer | 20 ns | **+8.14 ns** | 50 MHz feasible |
| top, OS=2 | 20 ns TX / 10 ns RX | **−17.13 ns**（306 paths） | not feasible |
| top, OS=2 | 40 ns TX / 20 ns RX | **+2.87 ns** | 25 MHz feasible; 1 hold violation |

The serializer and deserializer individually reach 20 ns (50 MHz), but a
2×-oversampled full link at 20 ns needs a 10 ns RX clock and fails by 17 ns.
A 20 ns full link on GF180 5 V therefore needs a source-synchronous receiver
(no 2× CDR) or a different CDR architecture.  The 25 MHz link (40 ns TX /
20 ns RX) closes setup; the single hold violation is a post-CTS hold fix.

## 4. Wire delay by process class

Use the PDK's own RC, not a node-name assumption.  For a distributed RC wire:

\[
t_{wire} \approx \frac{1}{2} r c L^2,\quad r=\frac{R_{sheet}}{W}
\]

Pre-PnR STA should use the top routing layer actually used for the long bus,
not the default local layer, and add a margin.

| Class | Typical examples | Pre-PnR wire margin | Local span, no register | Medium span, one register | Long span |
|---|---|---:|---|---|---|
| 0.35 µm+ | 0.5/1 µm, 5 V | 25–35% | <100 µm | 100–300 µm | >300 µm: partition/pipeline |
| 0.13–0.18 µm | sky130, IHP, gf180 | 15–25% | <200 µm | 200–800 µm | >800 µm: partition/pipeline |
| 55 nm | icsprout55 | 20–30% | <100–200 µm | 200–500 µm | >500 µm: repeaters/pipeline |

These spans are engineering guardbands `[INFERENCE]`; replace them with the
target PDK's RC and the actual floorplan.  At 55 nm, wire RC can be a larger
fraction of the clock period than at 180 nm because wire width shrinks while
capacitance per length does not.

Worked example from the icsprout55 tech LEF:

```text
MET3: Rsheet = 0.0914 ohm/sq, width = 0.1 um, CPERSQDIST = 0.0011069 pF/um^2
r = 0.0914 / 0.1 = 0.914 ohm/um
c = 0.0011069 * 0.1 = 0.00011069 pF/um = 0.11069 fF/um
L = 1 mm: R = 914 ohm, C = 110.7 fF, 0.5*R*C = 50.6 ps
```

That 50 ps is only the distributed-RC term; vias, driver resistance, and
repeater spacing add to it.  For a 5 ns clock this is 1%, but for a 200 ps
clock it is 25% and must be budgeted explicitly.

## 5. Pre-PnR signoff checklist

1. Generate a per-block SDC with the real clock period.
2. Exclude async reset and static enables from data-path STA; document the
   exclusion.
3. Report data-path WNS separately from the `async` path group.
4. Set wire RC for signal and clock to the actual top layers.
5. Apply the class wire margin to the pre-PnR result.
6. Check max fanout/slew/capacitance on reset, lock, enable, and clock nets.
7. After CTS: insert reset tree, re-enable recovery/removal, and re-run.
8. After PEX: replace estimated wire RC with SPEF and repeat.

## 6. Target envelopes from current data

| Class | Serializer target | Deserializer target | Required change |
|---|---:|---:|---|
| 0.35 µm+ | 10–25 MHz | 10–25 MHz | word-group capture, 4-level CTS |
| 0.13–0.18 µm | 100 MHz | 100 MHz+ | one-hot capture, 3-level CTS |
| 55 nm | 100 MHz | 100 MHz+ | word-group capture, 3-level CTS |

These are **pre-PnR, pre-CTS** envelopes, not signoff.  The serializer numbers
assume the load-enable fanout is buffered or captured in word groups.  Without
that, the 257-load control path dominates and the apparent fmax is much lower.
