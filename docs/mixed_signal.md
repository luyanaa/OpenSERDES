# Mixed-signal PEX chain (D start)

`verification/analog/mixed_signal_e2e.py` runs a transistor/RC transient:

```text
PWL bit stream
  -> TX Magic RC PEX (Inv_Transmitter)
  -> external R/C channel (100 ohm, 16 pF)
  -> RX Magic RC PEX (Resist_FB_INV)
  -> thresholded output samples
```

This is a first mixed-signal integration step, not the digital CDR/top-level
SoC E2E.  It checks:

- TX PEX polarity (inverting)
- RX feedback macro polarity (inverting; the two inversions restore the bit)
- signal swing through the extracted RC networks
- bit decisions after a simple channel

Current 8-bit, 100 Mb/s result:

```text
01011001 -> 0/8 errors, BER=0, best sample offset 0 ns
```

Run:

```bash
make mixed-signal-e2e
```

The sliced RX PEX output is also fed into the package's digital
`serdes_rx_digital` frame bench:

```bash
make rx-pex-digital-e2e PACKAGE=out/ci-soc
```

One 4-word/8-bit frame result:

```text
analog errors=0
digital frame_errors=0
recovered=[127, 32, 24, 138]
```

This closes the one-frame analog-PEX-to-digital-recovery boundary.  It is not
a full SoC channel/PVT/MC run.

## Closed-loop jitter tolerance through the PEX chain (P2)

`make pex-jtol-sweep` extends the one-frame boundary above into a jitter
tolerance sweep.  `verification/analog/pex_jtol_sweep.py` injects deterministic
sinusoidal jitter (`--sj-ui`, peak amplitude in UI, `--sj-freq-mhz`) into the
TX PWL edge times of the same TX-PEX -> RC -> RX-PEX transient, slices the
analog decisions, and replays them into the generated package's
`serdes_rx_digital` frame scoreboard.  Every point therefore exercises both
halves of the loop; the reported tolerance is a property of the whole chain,
not of either half alone.

SJ support lives in `verification/analog/mixed_signal_e2e.py` (`_pwl`).  It is
applied to edge times only, so bit values and nominal spacing are unchanged and
the recovered stream stays comparable against the unjittered reference.

Measured at 100 Mb/s, R=100 ohm, C=16 pF, SJ=2 MHz, two PRBS-7 frames per
point (`build/verify/pex_jtol.json`):

| SJ (UI, peak) | clean eye | analog bit errors | frame errors | verdict |
|---:|---:|---:|---:|---|
| 0.00 | 6.5 ns | 0 | 0 | pass |
| 0.30 | 3.5 ns | 0 | 0 | pass |
| 0.45 | 0.5 ns | 0 | 0 | pass |
| 0.60 | 0.0 ns | 7 | 2 | fail |
| 0.75 | 0.0 ns | 12 | 2 | fail |

Tolerated SJ = **0.45 UI** (4.5 ns peak at 100 Mb/s); the eye closes
monotonically 6.5 -> 3.5 -> 0.5 -> 0 ns and the first bit errors appear at
0.60 UI.  This is a measured property of the extracted transistor/RC chain
plus the digital frame bench under one channel and one SJ frequency.  It is
**not** a silicon jitter tolerance, a BER-vs-jitter mask, or a PVT/MC swept
result; `tolerated_sj_ui` in the JSON is the largest amplitude with zero frame
errors, not a guaranteed compliance limit.  Open items for this measurement
(single channel/frequency, no PVT/MC/BER mask, uncommitted artifacts) are
listed in [cross_pdk_pnr.md](cross_pdk_pnr.md#current-gaps-open-items).
