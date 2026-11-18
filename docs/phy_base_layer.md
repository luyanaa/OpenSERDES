# SerDes base layer for protocol PHYs (USB / PCIe)

This note records what changed after reviewing LiteICLink, LitePCIe's soft PHY
and PR #250, USB3-PIPE and LiteEth basex
(`docs/liteiclink_review.md`, `docs/protocol_phy_review.md`,
`docs/litepcie_ecp5_review.md`).

The frame of the change is: **a transport-only PCS can survive without these;
a PHY that carries a real protocol cannot.**  A PCIe or USB 3 link does not only
move words, it has to

- say whether a wait is *still progressing* or *stuck*,
- say *how often* something went wrong, not just *that* it did,
- hand a broken symbol up as a definite protocol error,
- align a direct control input with the data already in flight,
- and measure the two clocks it is comparing.

Everything below is in RTL, tested, and wired into `make sim` /
`scripts/regress.py`.

## 1. Link health: watchdogs and counters

| module | purpose |
|---|---|
| `rtl/link_watchdog.sv` | "I expect progress within N cycles" with a `timeout` pulse, a sticky `stuck`, and a saturating `timeout_count` |
| `rtl/saturating_counter.sv` | a counter that never wraps, with a `saturated` flag so "0 errors" and "we stopped counting" differ |
| `rtl/clock_cycles.sv` | Gray-coded cycle counter coherently readable across clock domains |

Wired into the SoC streaming wrapper:

| CSR / signal | meaning |
|---|---|
| `code_error_count` (CSR16) | 8b10b encode/decode/disparity errors, saturating |
| `link_drop_count` (CSR17) | "was framed, lost framing" transitions |
| `clock_tx_cycles` / `clock_rx_cycles` (CSR18/19) | matched pair, latched together by `CSR0[6]` |
| `CSR0[5]` | clear the two counters |
| `CSR7[8]` | `comma_lock_stuck`: the receiver never found framing |
| `CSR7[9]` / `CSR7[10]` | either counter has saturated |

The measured `clock_tx_cycles / clock_rx_cycles` ratio is the ppm term that
`verification/channel/phase_model.py` predicts.  Before this, the model could
say "you should see 100 ppm" and nothing could check it.

`pcs_8b10b_comma` grew a search watchdog (`LOCK_TIMEOUT`, `lock_timeout`,
`lock_stuck`) which is surfaced through `serdes_rx_digital`
(`COMMA_LOCK_TIMEOUT`, `comma_lock_timeout`, `comma_lock_stuck`).  It counts
*clock cycles without a comma*, not valid-bit edges: a silent line, or a line
carrying data with no K28.5, is exactly the case it exists to catch.

## 2. In-band error substitution

`pcs_8b10b_rx_stream` takes `SUBSTITUTE_ERROR` / `SUBSTITUTE_K` (default
K28.4 `0x9C`) and a `symbol_substituted` pulse.  A received decode or disparity
error is then forwarded as a reserved K code with `charisk` set, instead of a
plausible-looking data byte.

Why: without it the protocol layer can only notice via its own CRC, and a
store-and-forward stage in between happily transports the wrong word.  PCIe
6.3.1.4 specifies this exact substitution; USB3-PIPE implements it as
`RXErrorSubstitution`.

`SUBSTITUTE_ERROR=0` keeps the previous behaviour bit-for-bit, so nothing that
already worked changes.

## 3. TX control alignment

`rtl/tx_ctrl_align.sv` delays a direct control input by `DELAY_CYCLES` so it
lands *with* the words already in the transmit pipeline instead of cutting
them.  `DELAY_CYCLES=0` is the bypass (the "preempt is what I want" case, e.g.
a `clear`).

The test (`tb_tx_ctrl_align`) sweeps the delay against a modelled 4-cycle
pipeline and shows the exact hazard PR #250 found on the ECP5 DCU:

```
INFO: delay 0 delivered 4 of 8 words
INFO: delay 1 delivered 5 of 8 words
INFO: delay 2 delivered 6 of 8 words
INFO: delay 3 delivered 7 of 8 words
INFO: delay 4 delivered 8 of 8 words    <-- pipeline depth
INFO: delay 5 delivered 8 of 8 words
PASS: tb_tx_ctrl_align truncates below 4, aligned at 4+
```

A control input that is not deliberately aligned **truncates the frame it was
supposed to follow**.  Making the delay explicit is what turns that from an
accident into a decision.

## 4. Tests

| test | covers |
|---|---|
| `tb_link_health` | counter increments/saturates/clears; watchdog progress vs. stuck, self re-arm, clear; Gray counter tracks across domains and never goes backwards |
| `tb_tx_ctrl_align` | delay sweep: truncation below pipeline depth, full delivery at/after it |
| `tb_8b10b_rx_protocol` | clean group forwarded, corrupted symbol substituted with `charisk`, `symbol_substituted` pulses, `SUBSTITUTE_ERROR=0` differs, comma watchdog pulses and latches |

`make -j4 sim` = 90 PASS, `python3 scripts/regress.py` = 143/143.

## 5. What is deliberately *not* done

- **No lane-width negotiation.**  PCIe renegotiates x1/x2/x4/x8 and puts the
  unused lanes into electrical idle; our `LANES` is still a compile-time
  parameter and one bad lane takes the whole bonded link down.  Correct for a
  chiplet link, wrong under a protocol stack.  Recorded in
  `docs/protocol_phy_review.md` §1c, not implemented.
- **No electrical-idle path.**  USB3-PIPE's two-counter `RXIdleFilter` is the
  design to copy when one exists; today there is no idle detector to filter.
- **No raw-word APB tap.**  LitePCIe's `debug.py` treats it as a product
  feature; ours stops at counters and status bits.
- **`link_ok` is transport readiness, not protocol readiness.**  A protocol FSM
  must not follow it (see `docs/multilane.md`), and PR #250 shows why: the
  receiver losing signal while the transmitter is fine is not a reason to
  disable the state machine that is waiting for training sequences.
