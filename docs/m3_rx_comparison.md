# M3: RX route comparison

## Measured smoke comparison

Script: `verification/analog/m3_rx_compare.py`  
Data: `build/verify/m3_rx_compare.json`

Conditions:

```text
VDD = 1.8 V, load = 10 fF
DC sweep 0..1.8 V and 40 ns input pulse
same packaged canonical SPICE and same level-1 smoke models
```

| Metric | parallel stdcell RX | resistive-FB PMOS RX |
|---|---:|---:|
| DC trip | 0.794 V | 0.854 V |
| output swing | 1.800 V | 1.800 V |
| avg supply current | 0.465 uA | 1.096 uA |
| power | 0.835 uW | 1.972 uW |
| macro area | 49.14 um2 | 164.89 um2 |

The feedback RX has a +60 mV trip shift, about 2.36x supply current and 3.36x
area in this smoke setup.  These are **not** BER/eye or sensitivity results.

## What is not yet comparable

The following require a common channel, noise source, timing/clock model and
PDK transistor models:

- BER and eye margin at the same TX/channel/clock/input swing/noise;
- input sensitivity and noise margin;
- PVT and Monte Carlo on both routes;
- energy per bit and jitter.

The original feedback RX has separate PVT/MC/DC evidence.  The stdcell RX does
not yet have an equivalent analog characterization, so no claim of analog
superiority is made.

## Decision rule

The generator should keep both routes:

- stdcell RX: integration baseline, digital STA/DRC/LVS, no feedback claim;
- feedback RX: analog route, requires matching physical GDS + LVS/PEX and
  full channel/PVT/MC characterization before automatic selection.

## Same-channel BER rate sweep

Script: `verification/analog/m3_ber_sweep.py`  
Data: `build/verify/m3_ber_sweep.json`

Same TX PEX, 100 ohm / 16 pF channel, 32-bit frame, tt corner, sample-offset
search:

| rate (Mb/s) | parallel stdcell errors | feedback PMOS errors |
|---:|---:|---:|
| 25 | 0/32 | 0/32 |
| 100 | 0/32 | 0/32 |
| 200 | 0/32 | 0/32 |
| 400 | 0/32 | 0/32 |
| 800 | 2/32 | 1/32 |
| 1200 | 12/32 | 5/32 |
| 1600 | 16/32 | 6/32 |

At 800 Mb/s, ss/ff corner spot checks:

```text
feedback ss: 1/32, ff: 1/32
parallel ss: 10/32, ff: 2/32
```

This is a small-N rate/corner comparison.  `m3_ber_sweep.py` now reports a
Wilson 95% interval with every error count; the original 32-bit intervals are
too wide for a production BER claim.

A 128-bit PRBS-7 rerun at the two marginal rates gives:

| rate (Mb/s) | parallel errors / BER [95% CI] | feedback errors / BER [95% CI] | PCell feedback errors / BER [95% CI] |
|---:|---:|---:|---:|
| 800 | 21/128 = 0.164 [0.110, 0.238] | 14/128 = 0.109 [0.066, 0.175] | 14/128 = 0.109 [0.066, 0.175] |
| 1200 | 38/128 = 0.297 [0.225, 0.381] | 26/128 = 0.203 [0.143, 0.281] | 26/128 = 0.203 [0.143, 0.281] |

The PCell RX PEX (3N/6P + two series feedback PMOS) reproduces the original
feedback route at these points.  This is a topology/electrical consistency
check, not an independent process-variation result.

A 256-bit PRBS-7 rerun (two 127-bit periods plus two bits), with the same TX
PEX, 100 ohm / 16 pF channel, tt corner and sample-offset search, gives:

| rate (Mb/s) | parallel errors / BER [95% CI] | feedback errors / BER [95% CI] | PCell feedback errors / BER [95% CI] |
|---:|---:|---:|---:|
| 800 | 41/256 = 0.160 [0.120, 0.210] | 27/256 = 0.105 [0.0735, 0.149] | 27/256 = 0.105 [0.0735, 0.149] |
| 1200 | 73/256 = 0.285 [0.233, 0.343] | 51/256 = 0.199 [0.155, 0.252] | 51/256 = 0.199 [0.155, 0.252] |

The corresponding sampled eye widths are 0.0625 / 0.0417 ns (feedback),
0 / 0 ns (parallel), and 0.0625 / 0.0417 ns (PCell feedback) at 800 / 1200
Mb/s.  The intervals still overlap; this is **not** a statistically resolved
route selection.  A 32-bit ss/ff spot check at 800 Mb/s gives:

```text
800 Mb/s:
  ss: feedback 1/32, parallel 13/32
  ff: feedback 1/32, parallel 1/32
1200 Mb/s (128-bit PRBS-7):
  ss: feedback 32/128, parallel 50/128, PCell feedback 31/128
  ff: feedback 22/128, parallel 29/128, PCell feedback 22/128
```

Noise, input sensitivity, eye margin, full voltage/temperature PVT, global
process Monte Carlo, and a much larger error-count campaign remain open.  A
local-mismatch spot check is reported below.  No production selection is
claimed.

## Noise spot check

`verification/analog/m3_noise_sweep.py` injects a deterministic Gaussian
series voltage source (sigma 0 or 100 mV, seed 7) into the same 100 ohm /
16 pF channel and uses the same 32-bit frame.

| rate (Mb/s) | noise (mV) | parallel errors | feedback errors | PCell feedback errors |
|---:|---:|---:|---:|---:|
| 800 | 0 | 2/32 | 1/32 | 1/32 |
| 800 | 100 | 4/32 | 1/32 | 1/32 |
| 1200 | 0 | 12/32 | 5/32 | 5/32 |
| 1200 | 100 | 10/32 | 5/32 | 5/32 |

This is a small-N deterministic screening check, not a noise-margin or
production BER claim.  PCell feedback again tracks the original feedback PEX.
Input sensitivity, eye masks, and full Monte Carlo remain open.

## PDK local mismatch spot check

`verification/analog/m3_mc_sweep.py` enables the sky130 ngspice
`mc_mm_switch=1` hook and runs four deterministic seeds at 800 Mb/s with the
same 32-bit frame and channel.  This is per-device local mismatch only; the PDK
library does not wire a global-process switch into these models.

| route | per-seed errors / 32 | aggregate BER [95% CI] | eye height range |
|---|---:|---:|---:|
| feedback | 1, 1, 1, 1 | 4/128 = 0.031 [0.012, 0.078] | 1.7877–1.7895 V |
| parallel | 1, 1, 5, 1 | 8/128 = 0.0625 [0.032, 0.118] | 1.596–1.735 V |
| PCell feedback | 1, 1, 1, 1 | 4/128 = 0.031 [0.012, 0.078] | 1.7398–1.7428 V |

The parallel route has one visible outlier seed and a lower eye height; the
four-seed intervals overlap, so this is a mismatch screening check, not a
statistical route decision or a global-process MC result.

## Eye opening spot check

`mixed_signal_e2e.run` now reports a sampled eye width (range of sampling
offsets at the best error count) and a vertical eye height.  128-bit PRBS-7:

| route | rate | errors | eye width | eye height |
|---|---:|---:|---:|---:|
| feedback | 400 | 0/128 | 1.50 ns | 1.796 V |
| parallel | 400 | 0/128 | 1.25 ns | 1.799 V |
| PCell feedback | 400 | 0/128 | 1.50 ns | 1.788 V |
| feedback | 800 | 14/128 | 0.0625 ns | 1.797 V |
| parallel | 800 | 21/128 | 0 ns | 1.799 V |
| PCell feedback | 800 | 14/128 | 0.0625 ns | 1.792 V |

This is a sampled eye proxy, not a full bathtub/eye-mask or jitter
decomposition.  PCell feedback again matches the original feedback route.

## Input-swing spot check

`verification/analog/m3_sensitivity.py` sweeps the input high level at
800 Mb/s with the same 32-bit frame and 3-route wrappers.

```text
Vin high   feedback   parallel   PCell feedback
0.6 V      16/32      16/32      16/32
0.9 V       8/32      12/32       8/32
1.2 V       1/32       2/32       1/32
1.8 V       1/32       2/32       1/32
```

This frame does not produce a zero-error point at 1.8 V, so it cannot establish
a minimum sensitivity; it only shows the PCell route tracks the original
feedback route.  A proper sensitivity test needs a longer frame and controlled
sampling/CDR conditions and remains open.
