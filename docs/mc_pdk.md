# sky130 PDK statistical Monte Carlo in ngspice

## What the PDK exposes

The sky130 model cards carry commented Spectre statistics blocks.  For example
`sky130_fd_pr__nfet_01v8__tt.pm3.spice` has:

```text
* statistics {
*   mismatch {
*     vary sky130_fd_pr__nfet_01v8__toxe_slope_spectre dist=gauss std = 1.0
*     vary sky130_fd_pr__nfet_01v8__vth0_slope_spectre dist=gauss std = 1.0
*     vary sky130_fd_pr__nfet_01v8__voff_slope_spectre dist=gauss std = 1.0
*   }
* }
```

The same variables appear in the ngspice model expressions as
`MC_MM_SWITCH*AGAUSS(0,1.0,1)*...`.  The ngspice library defines
`mc_mm_switch` and `mc_pr_switch`; ngspice 44 evaluates `AGAUSS` itself, and
`set seed=N` controls the draw.  Therefore, **per-device PDK mismatch** is
available without translating the commented statistics block.

## Verified probe

`verification/analog/pdk_mc.py` drives two identical NMOS devices and reports
both common-mode current (global variation) and the device-to-device
difference (local mismatch):

```bash
make pdk-mc          # mismatch mode, 8 seeds
python3 verification/analog/pdk_mc.py --mode nominal
python3 verification/analog/pdk_mc.py --mode global   # fail-closed
```

Observed with 6 mismatch seeds:

- `differential_std = 6.3e-6 A` (device currents differ; true local mismatch)
- `common_std = 3.3e-6 A`
- seeds 1/2/3 produce different currents (`seed_effective = true`)
- `--mode nominal`: zero variation across seeds
- `--mode global` (`mc_pr_switch=1`, `mc_mm_switch=0`): **zero variation**;
  this PDK/library has no model expression using `mc_pr_switch`.  The global
  process section of `critical.spice` (67 `vary` entries) is not wired into the
  ngspice device cards, so global MC is reported unavailable rather than
  mislabeled.

## Why this is not just behavioral

The mismatch draw is inside the PDK device model expressions, not a series
gate-voltage offset.  Two instances with the same model see independent
`AGAUSS` draws.  The behavioral mode in `mc_runner.py` remains separate and is
never presented as PDK MC.

## Remaining gap

Global process Monte Carlo requires either a Spectre statistical run or a PDK
release that wires `mc_pr_switch`/global process variables into the model
cards.  This repository fails closed for that mode.
