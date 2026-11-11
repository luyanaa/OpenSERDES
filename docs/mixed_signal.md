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

## Jitter tolerance through the PEX chain: source-synchronous replay, NOT the 4x CDR

> **Correction (this section previously called this "closed-loop jitter
> tolerance").**  The digital half of `pex_jtol_sweep.py` runs
> `_compile_tb("frame", ..., oversample=1, cdr_mode=2, align_mode=2, ...)`,
> i.e. the **source-synchronous bypass**, not the `CDR_MODE=3` bang-bang
> loop.  The analog PEX chain already made the bit decisions at fixed sample
> offsets before the RTL sees anything.  So `tolerated_sj_ui` measures
> "TX PEX -> RC -> RX PEX + frame recovery tolerates this much sinusoidal
> edge displacement when the decisions are replayed", and **nothing about the
> 4x CDR's jitter tracking, jitter transfer or jitter tolerance**.  It must
> not be quoted as a CDR JTOL result.

`make pex-jtol-sweep` injects deterministic sinusoidal jitter (`--sj-ui`,
peak in UI, `--sj-freq-mhz`) into the TX PWL edge times of the same
TX-PEX -> RC -> RX-PEX transient, slices the analog output at a **fixed**
sample offset, and replays those decisions into the generated package's
`serdes_rx_digital` frame scoreboard.  SJ support lives in
`verification/analog/mixed_signal_e2e.py` (`_pwl`) and is applied to edge
times only.

Measured at 100 Mb/s, R=100 ohm, C=16 pF, SJ=2 MHz, two PRBS-7 frames per
point (`build/verify/pex_jtol.json`):

| SJ (UI, peak) | clean eye | analog bit errors | frame errors | verdict |
|---:|---:|---:|---:|---|
| 0.00 | 6.5 ns | 0 | 0 | pass |
| 0.30 | 3.5 ns | 0 | 0 | pass |
| 0.45 | 0.5 ns | 0 | 0 | pass |
| 0.60 | 0.0 ns | 7 | 2 | fail |
| 0.75 | 0.0 ns | 12 | 2 | fail |

What this **does** show: the extracted transistor/RC chain plus the digital
frame bench still recovers the frame when the TX edges move by up to 0.45 UI
peak at 2 MHz, with a fixed receive sample point.  What it does **not** show:
that a real CDR with an independent 4x clock tracking those edges would
recover them.  A CDR that tracks jitter can do better in the passband and
worse near its loop bandwidth; a fixed sampler is simply another transfer
function.

### Gap: real CDR_MODE=3 mixed-signal replay (open)

To measure actual CDR jitter tolerance the analog output must not be sliced
into ideal bits first.  The required setup:

1. keep the PEX output as a continuous waveform (or a dense list of
   threshold-crossing times), not fixed-offset decisions;
2. drive the RTL CDR with that waveform through `cdr_sync` on an
   **independent 4x RX clock**, so `phase_q`/centre/edge events decide the
   sample instants;
3. sweep multiple SJ frequencies (not one 2 MHz point), plus RJ and a
   frequency-step case, and report BER vs SJ amplitude per frequency
   (a JTOL mask), not a single amplitude.

Artifacts for that run are `build/verify/*` (gitignored, portable-by-default).
Until it exists, no 4x-CDR jitter-tolerance claim should be made.

