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

The intervals overlap; this is **not** a statistically resolved route
selection.  A 32-bit ss/ff spot check at 800 Mb/s gives:

```text
800 Mb/s:
  ss: feedback 1/32, parallel 13/32
  ff: feedback 1/32, parallel 1/32
1200 Mb/s (128-bit PRBS-7):
  ss: feedback 32/128, parallel 50/128, PCell feedback 31/128
  ff: feedback 22/128, parallel 29/128, PCell feedback 22/128
```

Noise, input sensitivity, eye margin, full voltage/temperature PVT/MC, and a
much larger error-count campaign remain open.  No production selection is
claimed.
