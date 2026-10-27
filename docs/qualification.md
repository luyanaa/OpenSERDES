# Qualification levels

A generated package is not a PHY because it synthesizes.  This project records
evidence at explicit, cumulative levels.  A higher level must not be inferred
from a lower one.

| Level | Required evidence | What it proves |
|---|---|---|
| RTL_FUNCTIONAL | lint, sim, synth | RTL parses, self-checking tests pass, generic synthesis has no latches/memories |
| PRE_PNR_TIMING | pre-PnR setup/hold WNS >= 0 | digital data path is plausible at the target period |
| POST_ROUTE_TIMING | post-route setup/hold WNS >= 0 | placed/routed digital timing closes at the target corner |
| PHYSICAL_SIGNOFF | DRC, LVS, antenna, CDC clean | physical implementation passes the PDK checks |
| LINK_VALIDATED | BER, jitter tolerance, eye margin | the link meets a stated BER under a stated channel/stress profile |

## 1. Report format

~~~json
{
  "ber_target": 1e-12,
  "checks": {
    "lint": true,
    "sim": true,
    "synth": true,
    "pre_pnr_setup_wns": 0.12,
    "pre_pnr_hold_wns": 0.01,
    "post_route_setup_wns": 0.04,
    "post_route_hold_wns": 0.008,
    "drc_clean": true,
    "lvs_clean": true,
    "antenna_clean": true,
    "cdc_clean": true,
    "ber": 1e-13,
    "jtol_pass": true,
    "eye_margin_ui": 0.18
  }
}
~~~

Run:

~~~bash
python3 scripts/qualify.py --report signoff.json --level PHYSICAL_SIGNOFF \
  --out qualification.json
~~~

The script computes the highest cumulative level whose checks all pass.  It
exits non-zero if the requested level is not reached.

## 2. Rules

- Pre-PnR WNS is not post-route WNS.  Do not put pre-PnR numbers in a
  POST_ROUTE_TIMING report.
- Zero observed errors is not a BER measurement by itself.  For independent
  rare errors and 95% one-sided confidence, N bits with zero errors bound
  BER <= 3/N; 1e-12 therefore needs about 3e12 bits.
- Jitter tolerance is measured with the real CDR at a swept SJ frequency and
  amplitude, not by eye-diagram inspection alone.
- Eye margin must state the channel, jitter, voltage noise and BER contour.
- The first link validation should be source-synchronous, so that TX/RX/analog
  issues are separated from CDR tracking issues.
