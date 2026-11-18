# LitePCIe PR #250 review — ECP5 Gen1 x1 soft PHY

Reviewed: <https://github.com/enjoy-digital/litepcie/pull/250> (Dirk Eibach),
diff `patch-diff.githubusercontent.com/raw/enjoy-digital/litepcie/pull/250.diff`
(21 files, ~102 kB).  New files read in full: `litepcie/phy/soft/serdes/ecp5.py`
(327 lines), `litepcie/phy/soft/serdes/ecp5_model.py` (151 lines),
`bench/soft/versa.py` (167 lines), `test/test_soft_ecp5.py`,
`test/test_soft_ecp5_lane.py` (236 lines), plus the `doc/softphy.md` and
`doc/manual/*` hunks.

This is the **first fully open-source PCIe endpoint on an FPGA with an
open-source toolchain** (Yosys/nextpnr), running LitePCIe's soft PHY on a
Lattice ECP5 DCU at 2.5 GT/s x1.  It matters to us less as a PCIe story and
more as a *bring-up evidence* story: it is an existence proof that a
fabric-implemented PCS + protocol stack enumerates on a real host, and it
documents every trap it hit.

## Hardware result (their claim, not ours)

| metric | value |
|---|---|
| board / host | ECP5 Versa LFE5UM-45F, Fujitsu FUTRO S920 (AMD GX-222GC, root port 2.5 GT/s x4) |
| enumeration | yes, bitstream from SPI flash so the card links during POST |
| `dma_test` loopback | 1.66 Gb/s, 0 errors |
| 15 min DMA soak | 22.8M buffers, 0 errors, no replay, no receiver error, no AER |
| 20 link cycles (unload/remove/Retrain Link/rescan/load) | 20 of 20, link back in 0.6 s |
| BAR0 stress, 15 min | 106.5M write+readback, 426.0M memory words, 0 mismatches |
| nextpnr | `pcie_link` 75 MHz target (closes 82.8), `sys` 62.5 (closes 74.7), lane 62.5 (closes 87.7) |
| resources | 24400 LUT4 (55%), 10793 FF (24%), 56/108 DP16KD |

Note what they *do not* claim: no 5 GT/s (the non-5G part cannot), no ASPM, no
second host, no root port.  That honesty is the right template for our own
`docs/` — and it is why the numbers above are quotable at all.

## What is directly useful to us

### 1. `tx_idle_delay` — the control input overtakes the data pipeline

The most transferable finding, and it applies to any fabric PCS:

> `tx_idle_delay`: lane clock cycles the TX Electrical Idle request is delayed
> (both edges) so that it reaches the line with the data: the EIOS is still in
> the TX pipeline (lane datapath, 8b/10b, gearbox, DCU) when the LTSSM asserts
> `tx_elecidle`, and `FFC_EI_EN` is a direct input (the Xilinx transceivers do
> this with `TX_EIDLE_ASSERT_DELAY`).

They added a **CSR to tune it on hardware** ("2 from simulation … to be tuned on
hardware through the `tx_idle_delay` CSR (the DCU's own pipeline is not
modeled)"), and the test asserts the bug is real:

```python
def test_tx_idle_delay(self):
    self.assertEqual(len(self.eios_symbols_on_the_line(0)), 0)
    self.assertEqual(len(self.eios_symbols_on_the_line(1)), 0)
    self.assertEqual(len(self.eios_symbols_on_the_line(2)), 4)  # COM + 3 IDL
```

**This is a class of bug we have not tested for.**  In our SoC wrapper,
`tx_clear_pulse` and `rx_flush_pulse` are generated from the APB domain and
asserted directly on the TX datapath — they are not delayed to match the words
already in flight, and `tx_core_enable = tx_enable_tx & ~bist_enable_tx` is
likewise a level that reaches the serializer immediately while the frame in the
pipeline is still being shifted out.  For `tx_clear` that is *intended*
(discard in-flight data), but there is no test that says so, and no equivalent
of "shut the driver down cleanly, after the last symbol".

Borrow: (i) a `TX_CTRL_DELAY` parameter on the paths where a direct control must
*align* with data rather than preempt it, (ii) a TB in the shape of
`test_tx_idle_delay` that sweeps the delay and asserts 0 / 0 / N symbols on the
line.  Even if we conclude "preempt is correct for `clear`", the test is what
makes that a decision instead of an accident.

### 2. A behavioural transceiver model with knobs, in 151 lines

`ecp5_model.py` models only what the adapter depends on — and says so:
*"It models only what the adapter relies on (assumptions of the model, not
measured behavior)"*.  Knobs: `bit_offset`, `tx_lock_cycles`,
`rx_lock_cycles`, `detect_cycles`, `present`, `force_los`, `corrupt`,
`line_out`/`line_in` (so two models connect), and full SCI register emulation
(64×8 with the polarity bits at register 0x01).

Compare with our `channel_model.sv`: `DELAY_CYCLES`, `BIT_ERROR_PERIOD`,
`INVERT`, `corrupt_pulse`, `slip_pulse`.  They have **receiver detection and
loss-of-signal** as first-class model inputs; we have neither, and we have no
"two DUTs connected" harness for the PCS layer.

Borrow: extend `channel_model` with `LOS` (force loss of signal) and a lock
latency, and add `bit_offset` as a *parameter* so the offset sweep below is a
one-liner.

### 3. Systematic bit-offset and polarity sweep

```python
def test_loopback_bit_offsets(self):
    # The raw 10BSER bus has no comma alignment: TS Ordered Sets and packets
    # received at any bit offset.
    for offset in [0, 1, 4, 9]:
```

and

```python
def test_polarity(self):
    # Both inverted: corrected (TX inversion undone by the RX inversion).
    # Only the TX inverted: nothing decodable.
```

They sweep the misalignment *systematically* rather than once, and they test the
**asymmetric** polarity case explicitly (TX inverted, RX not → must not decode).
Their stream check is also worth copying: they search the received stream for
the expected sequence (`any(received[i:i+n] == expected …)`) instead of
requiring a fixed start offset, which is correct for a link whose lock latency
is not guaranteed.

Our `tb_pcs_8b10b_realign` has *one* deliberate misalignment ("push only 6 bits
of an arbitrary symbol to misalign by ~4 bits") and one polarity implied by the
channel.  Borrow the loop over offsets {0,1,4,9} and the negative polarity
assertion.

### 4. Receiver detection is a state machine, with counters

`ECP5PCIeDetect` is 60 lines of FSM: `IDLE → DETECT-MODE (≥120 ns lead) →
STROBE (4 cycles) → WAIT-DONE-LOW → WAIT-DONE-HIGH → FINISH`, with a `timeout`
that reports "no receiver", and **three bring-up counters**
(`n_requests`, `n_done`, `n_timeouts`) exposed as a CSR.

The pattern — *a vendor handshake with an explicit lead time, an explicit
strobe, an explicit asynchronous done in both directions, and a timeout that
produces a definite answer* — is the same shape as the link-up watchdogs noted
in `docs/liteiclink_review.md`, now confirmed in a second, harder context.  Also
note `WAIT-DONE-LOW` exists because "the DCU drives done low asynchronously
after the strobe (a few samples may still read high)": a two-phase wait around
the handshake is the correct way to sequence an asynchronous done.

We have `tx_detect_rx` only in `serdes_pipe_lite_top` (a one-cycle PhyStatus
stub, documented as such).  No action beyond noting the FSM shape.

### 5. Clocking: derive both link clocks from the *host's* reference

`ECP5PCIeClocking`: `pcie_pipe` = DCU TX clock / 2, and `pcie_link` from a
fabric PLL fed by the DCU's 125 MHz TX clock, so both derive from the host's
100 MHz reference (common-clock architecture); `sys` stays on the local
oscillator and the PHY crosses between them.  Two documented dead ends:
"the EXTREFB output can't be routed to a fabric PLL input by nextpnr (tried
first)", and `pcie_link` is held in reset while the DCU TX PLL is unlocked.

Directly relevant to `docs/clocking.md`: our SoC wrapper already runs
`pclk` / `s_axis_aclk` / `m_axis_aclk` / `tx_clk` / `rx_clk` independently and
crosses with FIFOs — the same conclusion reached from the other direction — but
we do not have the "hold the link clock in reset until the source PLL locks"
pattern, which is what prevents a burst of garbage words at bring-up.

### 6. Their `ECP5PCIeSerDes` sets `lane.ready` from **TX only**

```python
# Ready: TX only. The DCU init FSM clears rx_ready whenever the receiver loses
# its signal (partner in Electrical Idle) and relocks by itself when it comes
# back; the soft PHY's LTSSM is disabled while not ready, so it must not follow
# the RX signal (the host's TS1 are what we wait for).
self.specials += MultiReg(gt.tx_ready, lane.ready, "pcie_pipe")
```

This is a **deliberate decoupling of "the link is ready" from "the receiver
sees signal"**, because the protocol's own training sequences are what the state
machine waits for.  Compare our `link_ok = aligned & all lane_locked & no
lane_fault`: it *does* follow the RX signal, which is right for a bonded link
whose only job is transport, and wrong the moment a protocol state machine sits
above it (a partner that idles would take `link_ok` down and stall the protocol).
Worth an explicit note in `docs/multilane.md`: `link_ok` is a *transport*
readiness signal, not a protocol one.

They keep `rx_ready` as separate status (`self.rx_ready`) precisely so telemetry
and control do not share one meaning.

### 7. Bring-up CSRs are named for the bring-up

`_dcu_status` exposes `tx_lol`, `rx_lol`, `rx_los`, `tx_ready`, `rx_ready`, and
`init_state` coded as the position in a *named list*
(`["RESET-ALL", "WAIT-TX-PLL-LOCK", …]`) so a human reads a state name, not a
number.  `_detect_status` exposes the three counters.  That is the same lesson
as `docs/protocol_phy_review.md` §1d (raw tap + event trace as a product
feature), applied at the transceiver layer.

## What does not transfer

- **Vendor-specific plumbing**: `EXTREFB`, DCU `PCIE_MODE` attributes,
  `tx_idle_delay` on `FFC_EI_EN`, `TN1261` receiver detection — all ECP5 DCU
  specifics.  The *pattern* transfers (see item 1 and 4), the code does not.
- **Their resource headroom**: 55% LUT4 for a Gen1 x1 endpoint.  Our PCS is
  smaller, but this is the realistic cost of a fabric PHY + protocol stack, and
  worth remembering before claiming a cheap PCIe path.
- **`sys` at 62.5 MHz because the DMA logic does not close at 75 MHz**: a
  nextpnr/ECP5 limitation, not a design statement.

## Concrete follow-ups

| # | item | source | size |
|---|---|---|---|
| 1 | `TX_CTRL_DELAY`: delay a direct TX control input so it lands *after* the in-flight symbols; `tb` sweeps the delay and asserts 0/0/N on the line | §1 | small |
| 2 | `channel_model`: `LOS` input + `LOCK_CYCLES` latency; `BIT_OFFSET` as a parameter | §2 | small |
| 3 | `tb_pcs_8b10b_realign`: sweep `bit_offset ∈ {0,1,4,9}` and assert the asymmetric TX-only-invert case does *not* decode | §3 | small |
| 4 | note in `docs/multilane.md` that `link_ok` is transport readiness, not protocol readiness, and that a protocol FSM must not follow it | §6 | docs |
| 5 | "hold the link clock in reset until the source PLL locks" note in `docs/clocking.md` | §5 | docs |
| 6 | name the bring-up CSR state fields (`init_state` as a named list) in `docs/soc_streaming.md` | §7 | docs |

Item 1 is the only one with a real bug class behind it; 2 and 3 are cheap
coverage that we currently lack entirely.
