# OpenSERDES SI / BER Verification Framework

This directory is a self-contained Python Signal-Integrity and BER framework for
the OpenSERDES NRZ link.  The behavioral/statistical layers are not a PDK/PEX
flow and contain no CDR; a separate iverilog-driven path drives the real
``cdr_adaptive`` RTL with a jittered edge schedule for phase-2 JTOL.  The
framework makes the link conventions explicit and provides a reproducible
stressed-eye/bathtub/BER baseline.

Dependencies are deliberately limited to `numpy`, `scipy`, `matplotlib` and
`PyYAML`.  `scikit-rf` and `cocotb` are **not** required.  Matplotlib uses the
Agg backend in the CLI and tests.

## Quick start

Run from the repository root:

```bash
# All unit tests (no RTL, no cocotb)
/usr/bin/python3 -m unittest discover -s verification/tests -v

# 2-UI stressed eye
/usr/bin/python3 -m verification.cli eye \
  --profile verification/profiles/stressed.yaml \
  --out /tmp/opencode/si_eye.png --bits 8000

# Empirical bathtub + JSON data
/usr/bin/python3 -m verification.cli bathtub \
  --profile verification/profiles/stressed.yaml \
  --out /tmp/opencode/si_bathtub.png \
  --json /tmp/opencode/si_bathtub.json --bits 8000

# Analytic JTOL demo (Gaussian tail, no RTL/CDR)
/usr/bin/python3 -m verification.cli jtol \
  --profile verification/profiles/stressed.yaml \
  --out /tmp/opencode/si_jtol.json --model analytic

# Real RTL CDR JTOL (iverilog + rtl/cdr_adaptive.sv, 12.5 Mb/s harness)
/usr/bin/python3 -m verification.cli jtol \
  --profile verification/profiles/stressed.yaml \
  --out /tmp/opencode/jtol_rtl.json --model rtl --bits 800 \
  --freqs 100000 500000 2000000 --amps 0.1 0.2 0.4

# Single RTL run / direct metrics
/usr/bin/python3 -m verification.rtl.runner --nbits 800 --sj-peak-ui 0.2 --sj-freq-hz 500000
```

The default profile is `verification/profiles/stressed.yaml`.
`--bits N` overrides the profile's `n_bits` for a faster or longer run.

`jtol --model rtl` runs iverilog with the real RTL.  ``--chain cdr`` drives
``rtl/cdr_adaptive.sv`` directly with a jittered edge schedule; ``--chain rx``
drives the full ``rtl/serdes_rx_digital.sv`` chain (CDR + sampler +
deserializer) with the *sliced analog waveform*: the Python channel model adds
ISI and AWGN on an 80-samples-per-UI grid, thresholds it, and the RTL recovers
bits from that binary stream.  Both paths report BER, bit slips and lock losses
from recovered RTL bits.  The harness clock is derived from the profile rate at
OVERSAMPLE=8 (the default ``runner.py`` rate is 12.5 Mb/s; the CLI uses the
profile rate).

## What each layer does

| User-facing question | Module | What it actually computes |
|---|---|---|
| What test pattern is used? | `patterns/prbs.py` | PRBS7/15/31 generated with the exact `rtl/prbs_gen.sv` recurrence; LSB-first word packing |
| How much timing error is on each bit boundary? | `channel/jitter.py` | Per-boundary TIE from RJ, SJ/PJ, DCD, injected DDJ and injected wander/SSC |
| What does the link do to the waveform? | `channel/channel_impulse.py` | First-order RC loss + delayed reflection taps, DC-normalized; optional NRZ waveform builder |
| What does the receiver decide? | `channel/rx_slicer.py` | Ideal **fixed-phase** threshold sampler; explicitly not a CDR |
| What does the eye look like? | `analysis/eye.py` | 2-UI folding, inner-rail eye height, threshold-crossing eye width, optional plot |
| What BER is observed? | `analysis/bathtub.py` | Empirical phase scan and phase x threshold grid; zero-error rule-of-three bound |
| What BER does a Gaussian tail predict? | `analysis/ber.py` | Rail mean/sigma, Gaussian Q-tail BER, Wilson/rule-of-three intervals |
| How does margin vary with SJ frequency/amplitude? | `analysis/jtol.py` | Generic `simulate(stimulus)` sweep driver and last-passing JTOL curve |
| Does the real CDR track the jitter? | `rtl/runner.py` + `rtl/tb_cdr_jtol.sv` | iverilog drives `rtl/cdr_adaptive.sv` with a jittered PRBS edge schedule; Python computes BER/bit slips from recovered bits |
| How do I run a standard scenario? | `profiles/*.yaml` + `cli.py` | Stable YAML schema and `eye`/`bathtub`/`jtol` commands |

## Capability gate

`scripts/verify_link.py` combines the behavioral eye with the RTL JTOL sweep and
writes a qualification-compatible `ber.json`:

```bash
python scripts/verify_link.py --profile verification/profiles/stressed.yaml \
  --out build/verify/ber.json --package out/soc
```

The gate uses the 3/N upper bound when the RTL run observes zero errors, so a
clean short run can never claim BER=0.  `eye_margin_ui` is the 2-UI
threshold-crossing width, not a BER contour.  `jtol_pass` requires every swept
frequency to reach the configured peak amplitude with no lock losses.  The
capability block records the profile SHA-256, chain, rate, ppm and git commit;
`pvt_validated` is always false because no PVT/PEX model is bundled.

## Conventions (read this before comparing numbers)

* **NRZ levels:** logical 0 = **-0.5 V**, logical 1 = **+0.5 V** (1 V nominal
  p-p).  The default decision threshold is therefore 0 V.  `nrz_waveform()`
  accepts custom levels.
* **UI:** `UI = 1 / rate`.  At 100 Mb/s, 1 UI = 10 ns.
* **Boundary/TIE:** nominal boundary `n` is at `t_n = n*UI + tie_n`.  An N-bit
  record has N+1 boundaries.  `build_tie()` returns `tie` in **seconds**;
  `tie / UI` is the dimensionless UI value.  Positive TIE moves an edge later.
* **SJ amplitude:** `sj_peak_ui` is **peak (0-to-peak)**.  Peak-to-peak is
  `2 * sj_peak_ui`; both are stored in the metadata dict.
* **RJ:** independent Gaussian samples per boundary.  This is not a colored
  phase-noise model and has no 1/f or cycle-to-cycle correlation.
* **DCD:** `+dcd_peak_ui` on 0→1, `-dcd_peak_ui` on 1→0, zero where the data
  does not transition.  Real DCD polarity/asymmetry may differ.
* **Sampling:** the fixed-phase sampler uses
  `floor((n + phase_ui) * sps)`, clipped to the waveform bounds, and discards
  the first `discard` bits.  Phase 0.5 UI is the nominal center of a bit when
  the waveform starts at a boundary.
* **PRBS:** output is the state MSB before the shift; the state is shifted
  numerically left and feedback enters at bit 0.  Seed is all ones by default.
  PRBS7 taps are `state[6]^state[5]`, PRBS15 `state[14]^state[13]`, PRBS31
  `state[30]^state[27]`.  First PRBS7 bits from all-ones: `1 1 1 1 1 1 1 0`.
  `prbs_words` packs the first serial bit into bit 0 (LSB-first).
* **Channel:** `alpha = exp(-1/(sps*tau_ui))`, first-order RC, then delayed
  echoes `+0.20` at 1 UI and `-0.08` at 2 UI (configurable), then division by
  `1 + sum(coefficients)` = 1.12 by default.  Long-run DC gain is 1.
* **Eye height:** 0.5th percentile of the upper rail minus 99.5th percentile of
  the lower rail at the center column.  **Eye width:** interpolation of
  threshold crossing times; the inner 0.5/99.5 percentile span, in UI.  These
  are transparent estimator choices, not a formal BER contour standard.

## Profiles

`verification/profiles/{clean,stressed,worst_case}.yaml` share this schema:

| Key | Unit | Meaning |
|---|---|---|
| `rate_mbps` | Mb/s | Link rate; `UI = 1 / (rate_mbps * 1e6)` |
| `prbs_width` | 7/15/31 | PRBS polynomial width |
| `n_bits` | bits | Record length (CLI `--bits` overrides) |
| `sps` | samples/UI | Waveform oversampling |
| `rj_rms_ui` | UI RMS | Independent Gaussian RJ per boundary |
| `sj_peak_ui` | UI peak | Sinusoidal jitter peak; p-p = 2x |
| `sj_freq_hz` | Hz | SJ frequency, or use `sj_freq_per_ui` instead |
| `dcd_peak_ui` | UI peak | DCD magnitude, signed by data transition |
| `noise_rms_v` | V RMS | Independent Gaussian sampler noise |
| `channel.tau_ui` | UI | RC time constant |
| `channel.reflections` | list of `[delay_ui, coeff]` | Delayed echo taps |
| `phases.start/stop/count` | UI, UI, int | Bathtub scan grid |
| `threshold_v` | V | Decision threshold |
| `discard` | bits | Leading bits omitted by analysis |
| `seed` | int | RNG seed for jitter and noise; PRBS reset seed remains all ones |

Profile values:

| Profile | RJ RMS | SJ peak | DCD peak | Noise RMS |
|---|---:|---:|---:|---:|
| `clean` | 0.00 UI | 0.00 UI | 0.00 UI | 0.005 V |
| `stressed` | 0.02 UI | 0.10 UI @ 400 kHz | 0.02 UI | 0.060 V |
| `worst_case` | 0.05 UI | 0.20 UI @ 400 kHz | 0.04 UI | 0.120 V |

These are a 100 Mb/s demo set, not silicon corners.

## Outputs and zero-error interpretation

* `eye` writes a PNG and prints UI, SJ peak/p-p, eye height and eye width.
* `bathtub` writes a semilogy PNG and optional JSON with phases, errors, BER,
  `upper95`, `zero_error` and best-phase summary.  Zero-error points are plotted
  at their `3/N` upper bound so they remain visible on a log axis.
* `jtol` writes JSON, CSV and a PNG.  `--model analytic` is a **Gaussian-tail
  stressed-eye demo** with a fixed sampling phase.  Its chart label and stdout
  say so explicitly.  It uses a demo BER limit of `1e-3` by default because the
  simplified fixed-phase channel has deterministic ISI that makes a `1e-12`
  crossing uninformative; the reusable `jtol_curve()` API default remains
  `1e-12`.  `lock_losses` and `bit_slips` are zero placeholders because there is
  no CDR to lock or slip.

An empirical error count of zero means only "zero observed in N bits".  The
reported 95 % upper bound is the rule of three, `3/N`.  For example, 8,000 bits
(7,900 after discard) gives `3/7900 = 3.797e-4`; that is a bound, not a measured
BER.  Rows with one or more errors report the empirical BER and a `NaN`/null
upper-bound field, because the rule of three does not apply.

## Explicit non-claims

1. **The fixed-phase slicer is not a CDR.**  It has no phase detector, loop
   filter, frequency acquisition, lock detector, phase interpolator or jitter
   tracking.  All results are source-synchronous/fixed-clock margin results.
2. **The empirical BER floor is set by N.**  No amount of plotting can measure
   below roughly `1/N`; zero observed errors produce only a confidence bound.
   Use `required_bits_for_ber(1e-12)` = 3e12 bits for a 95 % zero-error bound.
3. **Gaussian tail BER is an extrapolation.**  It assumes approximately
   Gaussian, independent residual noise around each rail and a fixed threshold.
   It is truth-aided (known bits classify the rails) and must not be propagated
   through a nonlinear CDR, DFE, MLSE, AGC or adaptive equalizer.  It does not
   predict lock loss or bit slips.
4. **No PDK, PEX, package or Touchstone data.**  The channel is a first-order
   RC plus fixed echoes.  `load_touchstone_sdd21()` raises a clear error
   because scikit-rf and differential mixed-mode Sdd21 handling are not bundled.
5. **The RTL path is digital-only.**  `rtl/runner.py` executes the real
   `cdr_adaptive` state machine, but the TX is an ideal edge schedule: no analog
   driver, no channel S-parameters, no PEX, no slicer offset/noise model.  It
   also does not model a receiver AFE or a full `serdes_rx_digital` chain.
6. **The analytic JTOL demo is not a compliance JTOL curve.**  A fixed-phase
   sampler has no loop bandwidth, jitter peaking or frequency-dependent
   tracking, so its frequency dependence is weak by construction.
7. **No crosstalk, colored jitter, SSC loop, reflection polarity calibration or
   statistical eye/COM standard** is implemented.

## Phase-2 plan

* **cocotb + comma-aligned frames:** the full RX chain is exercised, but
  `ALIGN_MODE=0` so the PRBS stream has no comma framing.  Add a framed PRBS
  mode and check `rx_par_data`/frame lock in addition to `rx_serial_data`.
* **Standards-shaped JTOL masks:** compare the measured RTL curve against a
  defined mask, and separate sinusoidal tolerance from ppm tracking and RJ.
* **Jitter transfer / peaking:** measure CDR phase transfer, not only BER.
* **Touchstone Sdd21:** optional scikit-rf dependency, mixed-mode port
  conversion, interpolation, passivity/causality checks and package models.
* **Confidence intervals:** carry Wilson/Clopper-Pearson intervals through the
  CLI and add sequential stopping rules for long BER runs.
* **Jitter refinements:** colored/1/f phase noise, pattern-dependent DDJ from
  the channel step response, SSC/FM wander, and per-lane supply noise.
* **Equalization/DFE:** explicit nonlinear blocks, with BER predicted by
  time-domain simulation rather than the Gaussian-tail formula.
* **PDK/PEX:** replace the toy channel with extracted interconnect and
  transistor-level driver/receiver models where available.

The **source-synchronous fixed-phase mode is the first baseline**; CDR jitter
tracking is a separate axis.  Do not compare a fixed-phase bathtub to a CDR
JTOL mask as if they measured the same thing.

## Layout

```
verification/
  patterns/prbs.py              PRBS7/15/31 matching rtl/prbs_gen.sv
  channel/jitter.py             TIE: RJ, SJ/PJ, DCD, DDJ, wander/SSC
  channel/channel_impulse.py    RC + reflection channel and NRZ builder
  channel/rx_slicer.py          ideal fixed-phase sampler + Q function
  analysis/eye.py               2-UI folding, eye height/width, plot
  analysis/bathtub.py           empirical phase and threshold scans
  analysis/ber.py               Gaussian-tail BER and confidence helpers
  analysis/jtol.py              generic sweep driver + last-passing curve
  rtl/tb_cdr_jtol.sv            iverilog TB: jittered edges -> cdr_adaptive
  rtl/tb_rx_jtol.sv             iverilog TB: sliced analog -> serdes_rx_digital
  rtl/runner.py                 compile/run/analyse RTL CDR/RX, BER and slips
  profiles/*.yaml               clean / stressed / worst_case
  cli.py                        eye / bathtub / jtol commands
  tests/                        PRBS, jitter, bathtub, BER and RTL-JTOL tests
```
