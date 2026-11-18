# LitePCIe soft PHY / USB3-PIPE / LiteEth basex: protocol-layer review

Reviewed (BSD-2-Clause, EnjoyDigital):

- <https://github.com/enjoy-digital/litepcie/tree/master/litepcie/phy/soft> —
  `advantages.md`, `common.py`, `lanes.py`, `ordered_sets.py`
- <https://github.com/enjoy-digital/usb3_pipe> — `serdes.py`, `datapath.py`
- <https://github.com/enjoy-digital/liteeth/tree/master/liteeth/phy> —
  `serial/basex/{pcs,pcs32}.py`, `phy/__init__.py`

This is the **protocol layer**: what sits on top of a working PCS and turns it
into PCIe Gen1–3, USB 3.x SuperSpeed, or 1000BASE-X/SGMII.  These three are the
most demanding consumers of an inter-chip link and each leaves a clear mark on
the PCS contract.

## 1. LitePCIe soft PHY — the most relevant of the three

`litepcie/phy/soft` reimplements the PCIe PHY *in fabric* on top of LiteICLink
transceivers instead of using the vendor hard block, and its `advantages.md`
states exactly why: Gen3 on 7-Series GTX (hard block stops at Gen2), any quad,
several independent links, board-layout freedom, no vendor IP, and — the part
that matters to us — **full visibility**: LTSSM trace, per-lane status, raw
words, and error counters.

### 1a. `RXLaneDeskew` is a better-specified version of our `lane_deskew`

This is the direct comparison and it is humbling in a useful way:

| aspect | LitePCIe `RXLaneDeskew` | our `lane_deskew` |
|---|---|---|
| alignment event | **COM Symbol in TS1/TS2** (protocol ordered set) | dedicated `MARKER_TAG` word + epoch |
| phase, not just word, alignment | COM position within the 4-Symbol word selects a symbol shift (`shift 0..3`) | whole-word only |
| skew bound | declared from the spec: 2 words @2.5 GT/s, 1 word @5.0 GT/s, `max_skew=11` Symbols | `MAX_SKEW` parameter, elaborated check |
| "too far ahead" handling | per-lane `queued` symbol count vs. `queued[j] + max_skew + 1` → that lane **skips to its next COM** | none: a lane more than one epoch ahead forces a full re-hunt |
| lock quality | `locked` + **`stable` = 8 consecutive clean TS intervals** | `aligned` only |
| lane masking | `mask` input so lanes outside the negotiated width are ignored | none (all lanes must arm) |
| clear on partner idle | `clear` input drops lock on Electrical Idle | `lane_fault` is sticky, `rx_flush` clears |

Two borrowings stand out:

**(i) `stable` (lock quality) instead of a bare `locked`.**  A single clean
marker proves nothing; 8 consecutive clean ones is a real claim.  Our
`marker_ok` counter exists, but there is no output that says "the deskew has
been continuously clean", so a downstream consumer cannot distinguish a
just-locked link from a settled one.  Cheap to add, and it is the correct thing
to publish to a SoC.

**(ii) Per-lane "too far ahead → skip to next COM" instead of global re-hunt.**
Their `drops[i]` check lets a lane that is more than `max_skew` Symbols ahead
resynchronise on its own next COM rather than dragging every lane through a
re-hunt.  Our design drops *all* lanes on a mixed beat.  Their approach costs
at most one lane's worth of symbols instead of a full marker period; the
trade-off is that it needs the alignment event to recur often, which is exactly
what a Training Sequence gives them and what `MARKER_PERIOD` gives us.

**(iii) Symbol-phase (sub-word) deskew.**  COM can land at Symbol 0..3 of a
lane word, so the deskew corrects a 0–3 symbol phase *before* word alignment.
We assume whole-word alignment only.  For 20/40-bit transceiver words this
matters; for our 8-bit words it does not, but it is worth stating in
`docs/multilane.md` that our channel model is whole-word.

### 1b. Ordered sets are **generated word-aligned, detected anywhere**

`ordered_sets.py`: "Generation is word aligned (COM on Symbol 0) … Detection
works at any Symbol position on the descrambled stream (Training Sequences are
not scrambled, SKP Ordered Sets already removed): Symbol by Symbol state
machines, 4 Symbols per cycle."

That is a clean split we do not articulate: the TX *guarantees* the ordered set
lands on a word boundary, and the RX tolerates any position.  Our marker
insertion already guarantees word alignment by construction, and
`pcs_8b10b_comma` already searches at any bit position — but the invariant is
implicit in both.  Writing it down (and asserting it in the TB) protects the
multi-lane marker cadence from a future "let's just insert the marker here"
change.

Related: their `TSGenerator` completes the *current* ordered set before
honouring `enable` being cleared ("the current Ordered Set is completed when it
is cleared").  Our `serdes_multilane_top` does the same for markers/sync words,
but `pcs_8b10b_comma` and `axis_ordered_set_insert` do not have that guarantee
documented, and `axis_ordered_set_insert` can in principle be cleared mid-set
(`clear` resets `comma_pending_q`, which is fine) — worth an explicit note.

### 1c. Link **width** is negotiated; our lane count is a parameter

`lanes.py`: "The link width is negotiated in Configuration (4.2.6.3): each lane
answers with the Link and Lane Numbers it receives, the width is the largest of
x1, x2, x4, x8 with these lanes from Lane 0. Lanes above it go to Electrical
Idle. … beats are split into words for the lanes in use."

Our `serdes_multilane_top` hard-codes `LANES` and requires *every* lane to arm
before the deskew releases.  A degraded lane therefore takes the link down
rather than narrowing it.  For a chiplet link that is the wrong default: the
useful behaviour is "run at x2 instead of x4 and report `degraded`".

Borrow (design, not code): a `LANES_ACTIVE` runtime value (CSR-writable) that
feeds the deskew `mask` and the bonder/debonder width, plus a `degraded` status
output.  Their lane numbering also handles **lane reversal**, which they say is
not implemented — we should not claim it either.

### 1d. Debug interface as a product feature

`advantages.md` lists "LTSSM trace, per-lane status, raw words, Training
Sequence, DLLP, TLP and error counters, rate trace" as a first-class reason to
use the soft PHY, and `debug.py` / `bench/soft/status.py` implement it.  The
stated use is bring-up of difficult hosts and customers' slots.

Our equivalent is `csr_regs` + `lane_fault` + `marker_error` + `realign_count`
+ `link_ok`.  Missing: a **raw-word tap** (the descrambled words before framing,
readable over APB) and an **event trace**.  The raw tap is what turns "the link
doesn't lock" into "here is what we are actually receiving".

### 1e. Everything runs in Verilator, no vendor models

`advantages.md` §"Simulation and CI": "The whole stack runs in Verilator, from
the PHY to the DMA, with two PHYs connected, no vendor models
(`bench/soft/sim*.py`, `test/test_soft_sim.py`)."

We have Icarus-based TBs and `channel_model`, and our SoC wrapper tests are
already two-ended.  The gap is scale: they simulate a *whole protocol stack*
over two connected PHYs in CI.  Our `tb_soc_cdc_packetizer` is the same shape at
a smaller scale — a direction to grow, not a new idea.

## 2. USB3-PIPE — flow control and error handling on a real link

### 2a. `RXSKPRemover`: elastic buffer done as an insertion/removal shim

USB3 SKP Ordered Sets exist purely for clock compensation: the TX inserts one
SKP OS every ~354 symbols, the RX removes them, and the *removal itself* is the
elastic function.  `datapath.py` implements it as a 64/8-bit shift register
with `sr_bytes`, accepting a fragment "only if it fits in the Shift Register,
taking into account the word output on the same cycle" —
`pipe.source.ready.eq((sr_bytes <= 4) | (source.ready & (sr_bytes <= 8)))`.

That is a cleaner formulation than our `pipe_elastic_buffer` for the
USB3/PCIe-style "delete some words to absorb a clock difference" case, because
the depth accounting is in *bytes/symbols that may or may not be present*
rather than in FIFO entries.  Worth reading before the next elastic-buffer
change.

### 2b. `RXErrorSubstitution`: replace a bad symbol with a reserved K code

Received 8b10b decode/disparity errors are **substituted with K28.4** so the
downstream protocol sees a definite "broken symbol" instead of a plausible-looking
byte, and `with_disparity=False` is offered as a deliberate area/speed trade for
slow fabrics ("corrupted packets still detected by the Link Layer CRCs").

We flag `code_error` and, in `pcs_64b66b_ctrl_decode`, classify a bad block as
`K_ERROR` — but our 8b10b path passes the *decoded-looking* byte downstream
with only a sticky error bit.  A protocol layer that cannot see the substitution
must rely on a CRC to notice.  Borrowing: an optional
`SUBSTITUTE_ERROR_SYMBOL` mode in `pcs_8b10b_rx_stream` that emits a reserved
K code (we have `/I/`, `/S/`, `/T/` free-standing already) and sets `charisk`,
so the error is in-band and survives any downstream store-and-forward.

### 2c. `RXErrorMonitor`: LFPS is detected *by its decode errors*

"`errors` is set while decode errors are seen with no more than `window` RX
clock cycles between them … A LFPS signal (10-50 MHz square wave) sampled at
the 5 Gbps line rate never decodes as valid 8b10b symbols while SuperSpeed data
does: this is used to differentiate a Warm Reset LFPS from data."

That is a genuinely clever reuse: the error *rate* is the signal.  Our
`code_error` is a sticky level, so we cannot express it.  Combined with the
`code_error_count` proposed in `docs/liteiclink_review.md`, a short "errors
within a window" flag is a few lines — and it is the standard trick for
low-frequency signalling on top of a high-speed link.

### 2d. `RXIdleFilter`: debounce electrical idle, with a documented *stretch*

`idle_cycles` consecutive idle cycles to report idle, `burst_cycles` consecutive
non-idle to report a burst, explicitly so a 32 ns spike seen on an Artix-7 GTP
board is rejected, with the constraint "keep it below the shortest LFPS burst
(40 ns)" and the warning that a burst is reported stretched by
`idle_cycles - burst_cycles`.

We have no electrical-idle path at all (documented as a limit in
`docs/pcs_coding.md` / `docs/raw_pcs.md`).  If one is added, this is the
parameterisation to copy: two counters, an asserted inequality, and the
stretch stated rather than hidden.

## 3. LiteEth basex — the ordered-set state machine done properly

### 3a. Idle words chosen to **preserve running disparity**

`pcs.py::PCSTX` IDLE state:

```python
Case(self.encoder.disparity[0], {
    0b0 : self.encoder.d[0].eq(D(5, 6)),   # /I1/ (Preserves disparity).
    0b1 : self.encoder.d[0].eq(D(16, 2)),  # /I2/ (Flips disparity).
})
```

Two idle words, selected by the current running disparity, so the idle stream
never drifts the disparity.  Our `pcs_8b10b_tx_stream` forces K28.5 at the start
of every frame and lets the encoder track disparity from there — correct, but
it does not give the *idle* stream a disparity-neutral encoding.  Same idea as
their `/I1/`, `/I2/` pair: worth adopting if an 8b10b link ever has to idle for
long periods.

### 3b. `PCSRX` is a strict, error-flagged ordered-set FSM

The RX FSM only accepts a data byte when `~decoder.k & ~decoder.invalid`;
anything else outside the expected ordered set goes to `ERROR` and emits
`source.error` + `source.last` so the MAC sees a truncated frame rather than
silence.  Configuration words (`/C1/`, `/C2/`) and idles (`/I1/`, `/I2/`) are
recognised as *distinct* accept paths.

Our `pcs_8b10b_rx_stream` has `code_error` and a `S_WAIT` timeout but no notion
of "expected ordered set here" — the consumer must validate.  For a raw PCS
that is right; for a framed link the expectation should be explicit.  Note as a
design option in `docs/pcs_coding.md`.

### 3c. `pcs32.py::Realiner32` — the frame realignment problem, solved by FSM

When the 32-bit datapath carries `/S/` at a non-zero byte position, the
remaining bytes must be shifted into the next word while `tlast`/`be` are
tracked per byte.  `Realiner32` is a 4-state FSM (`ALIGN_0..3` + `END_i`) that
does exactly that, with `be` (byte enable) arithmetic.

This is the byte-granular counterpart of our `axis_frame_packetizer`, which
works in *whole beats* and pads to `WORDS`.  Padding costs bandwidth; a
byte-enable realigner does not.  If the SoC streaming path ever needs to avoid
the 25% overhead noted in `docs/soc_streaming.md`, this is the shape of the fix
(carry `be` alongside `data`, realign instead of pad).

### 3d. `PCSTX32` handles `be`/`tlast` in one cycle per case

The `/T/` (end-of-frame) and `/R/` (carrier extend) insertion is a `Case` on
`sink.be`, i.e. the ordered set is placed according to **how many valid bytes
the last beat carried**.  Combined with 3c that is a complete "variable-length
frame over a fixed-width ordered-set link" solution — exactly the problem
`axis_frame_packetizer` + `axis_ordered_set_insert` solve for us, with padding
instead of byte enables.

## 4. Cross-cutting: what the protocol layer demands of a PCS

Reading all three together, the requirements they impose on the layer below are
consistent and worth writing into our own docs:

1. **The alignment event must be a protocol ordered set, not a private marker**
   (PCIe COM in TS, USB3 SKP, Eth `/I/`).  Our `MARKER_TAG` is private; that is
   fine for a bonded chiplet link, but it is why our deskew cannot be reused for
   a standard protocol without a shim.
2. **Ordered sets are word-aligned on TX, position-agnostic on RX.**
3. **The receiver must be able to distinguish "no data" from "broken data"**
   (K28.4 substitution, `invalid` flags, error-rate signalling).
4. **Clock compensation belongs to the link, not the protocol** (SKP
   insert/remove as an elastic shim).
5. **Lock quality must be published**, not just lock state (`stable`).
6. **Width/lane count should be able to shrink** rather than fail.
7. **A raw-word tap and an event trace are part of the product**, not debug
   scaffolding.

## 5. Concrete follow-ups

Compared with `docs/liteiclink_review.md`, this review adds protocol-driven
items.  Ordered by value:

| # | item | source | size |
|---|---|---|---|
| 1 | `deskew_stable`: N consecutive clean marker intervals (publish lock quality) | PCIe `stable` | small |
| 2 | per-lane "too far ahead → skip to next marker" instead of global re-hunt | PCIe `drops[i]` | small |
| 3 | `LANES_ACTIVE` mask + `degraded` status so a dead lane narrows the link | PCIe width negotiation | medium |
| 4 | `SUBSTITUTE_ERROR_SYMBOL` mode in the 8b10b RX (reserved K code, in-band error) | USB3 `RXErrorSubstitution` | small |
| 5 | `code_error`-in-window flag (error *rate* as a signal) | USB3 `RXErrorMonitor` | small |
| 6 | raw descrambled-word APB tap | PCIe `debug.py` | small |
| 7 | document the TX-word-aligned / RX-any-position invariant and assert it | PCIe `ordered_sets.py` | docs + TB |
| 8 | byte-enable realigner option to remove the padding overhead | Eth `Realiner32` | medium |
| 9 | disparity-neutral idle pair (`/I1/`, `/I2/`) for long idle streams | Eth `PCSTX` | tiny |
| 10 | two-counter electrical-idle debounce if/when an idle detector exists | USB3 `RXIdleFilter` | small |

Items 1, 2 and 5 are the same "link health" work already queued from the
LiteICLink review and should land together.

## See also

- `docs/litepcie_ecp5_review.md` — PR #250, the first open-source toolchain
  PCIe endpoint (ECP5 DCU, Gen1 x1) on this soft PHY: hardware evidence, and
  the `tx_idle_delay` finding (a direct control input overtakes the data
  pipeline, so it must be delayed to land with the ECP5 EIOS).
