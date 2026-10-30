# First mixed-signal PHY baseline (sky130)

`make ms-phy-baseline` runs the first mixed-signal PHY: a TX-PEX -> RC-channel
-> RX-PEX transistor transient chain on a **fixed sky130 PDK** at a **fixed
reference rate** (default 100 Mb/s), then a small PVT/noise screen so the
operating envelope is characterised rather than a single point.

## Chain

```
PRBS bits -> Xtx (Inv_Transmitter PEX) -> Rch/Cch channel -> Xrx (Resist_FB_INV PEX) -> bit decisions
```

Both macros are Magic RC PEX netlists (`make magic-pex` + `analog-macro-pex`),
not schematic approximations and not Calibre RVE.  The driver
`verification/analog/mixed_signal_e2e.py` builds one ngspice transient per
operating point, then does an **open-loop phase search**: a sampling-offset
grid is scored only on a training prefix, the centre offset is frozen, and the
held-out suffix is scored (`validation_ber`).  This is the same holdout method
as the M3 RX closure — it is an open-loop phase search, **not** a CDR loop.

## Runner

`verification/analog/ms_phy_baseline.py` wraps that chain:

1. **Baseline**: fixed sky130, 100 Mb/s, TT corner, 1.8 V, 27 °C, quiet channel.
2. **Rate sweep** (`--rates`): 100/200/... Mb/s at nominal PVT.
3. **PVT screen** (`--vdd-frac` × `--temps`) at the baseline rate.
4. **Noise screen** (`--noise-mv`) at the baseline rate.

Each point records errors, BER, `validation_ber`, `training_phase_span_ns`,
`eye_open_clean_ns`, `eye_open_ber_ns`, `eye_height_v`, the frozen
`sample_offset_ns` and the ngspice return code into
`build/verify/ms_phy_baseline.json`.

- `training_phase_span_ns` — span of sampling offsets that achieved the
  *minimum* training-prefix error count (a training-window diagnostic, not a
  BER-gated eye opening).
- `eye_open_clean_ns` — span of offsets with **zero** total bit errors (a
  BER=0 eye opening).
- `eye_open_ber_ns` — span of offsets with ≤5% total bit errors (a looser
  BER-criteria eye opening).

`MS_PHY_QUICK=1` runs only the nominal baseline point; `MS_PHY_BITS`,
`MS_PHY_RATES`, `MS_PHY_VDD_FRAC`, `MS_PHY_TEMPS`, `MS_PHY_NOISE_MV` tune the
sweep.

## Status (this checkout, baseline point)

| Point | Result |
|---|---|
| 100 Mb/s TT, 1.8 V, 27 °C, 0 mV noise | validation BER 0, clean eye opening 6.5 ns, eye height ~1.8 V |

## No overclaiming

- Each point is a single transient + one open-loop offset scan; the reported
  BER/eye are **diagnostic**, not a production BER interval or eye-mask claim.
- The phase search is open-loop; a CDR-conditioned sensitivity campaign is a
  separate layer (the digital CDR/training work).
- sky130 has no `.osdi` models in this install; ngspice emits non-fatal
  "couldn't be loaded" warnings for them and uses the built-in PDK models.
