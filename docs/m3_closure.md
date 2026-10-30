# M3 RX characterization closure

This is the **M3 characterization closure**, not production or SoC signoff.
The checker reports `production_qualified: false` even when it reports
`characterization_complete: true`.

## Holdout method

`verification/analog/m3_holdout.py` builds one transient from two independent
PRBS-7 streams:

- training prefix: 64 bits, seed `0x5D`;
- validation suffix: 64 bits, seed `0x33`.

The 19-point sampling-offset scan is scored **only on the training prefix**.
The centre of the best training region is frozen, then the validation suffix is
scored with that offset.  The validation bits never select the phase.  This is
still an open-loop phase search, not a CDR loop.

The campaign covered feedback, parallel stdcell and PCell feedback PEX routes;
800/1200 Mb/s; and 0/100 mV deterministic channel noise with seed 7.  All 12
cases completed without simulation errors; every case was written incrementally
to `build/verify/m3_holdout.json`.

## Holdout result

`validation_errors / 64` per case; each route has 256 validation bits total:

| route | 800 / 0 mV | 800 / 100 mV | 1200 / 0 mV | 1200 / 100 mV | aggregate |
|---|---:|---:|---:|---:|---:|
| feedback | 7 | 6 | 15 | 15 | 43/256 = 0.168 |
| parallel stdcell | 13 | 13 | 21 | 21 | 68/256 = 0.266 |
| PCell feedback | 10 | 8 | 15 | 15 | 48/256 = 0.188 |

Aggregate Wilson 95% interval over all 768 validation bits is diagnostic
`[0.180, 0.237]`; the rows are not independent production trials, so this is
not a production BER interval.  Training and validation error counts are close
(feedback 800: 6/64 training vs 7/64 validation; feedback 1200: 9 vs 15), which
is expected for this short, ISI-limited channel.

The feedback route is the best in this campaign.  The PCell feedback route ties
it at 1200 Mb/s but regresses at 800 Mb/s.  The parallel stdcell route is worst
at both rates.  The intervals overlap between feedback and PCell, so this is a
directional characterization result, not a statistically resolved production
selection.

## Other M3 evidence

The closure checker also requires the existing PVT, local-mismatch, bathtub,
noise, sensitivity and rate-sweep artifacts.  These remain spot checks and are
not upgraded to production evidence.

An analog-to-digital boundary check was added at 400 Mb/s with the feedback PEX:
four words, no analog bit errors and no digital frame errors
(`build/verify/m3_digital_e2e_400_w4.json`).  It is a source-synchronous frame
recovery check, **not** a CDR-conditioned sensitivity result.

## Status

Run:

```sh
make m3-holdout
make m3-closure
```

Current result:

```text
characterization_complete = true
production_qualified = false
holdout = 12/12 cases
validation bits = 768
validation errors = 159
aggregate diagnostic BER = 0.207
```

Open limitations:

- independent holdout is open-loop and short (64 validation bits/case);
- no CDR-conditioned sensitivity campaign;
- PVT/local-mismatch/bathtub are 32-bit spot checks;
- PCell feedback still lacks macro-level body tie/LVS and is not signoff-eligible;
- no global-process Monte Carlo for the M3 transient chain;
- no production/SoC BER or full eye-mask claim.

C strict remains held at `0.75 ns / 0.20 pF`; no strict ECO was run as part of
this M3 closure.
