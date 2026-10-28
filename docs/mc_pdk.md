# sky130 PDK statistical Monte Carlo: ngspice + Xyce

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
python3 verification/analog/pdk_mc.py --mode global   # ngspice: fail-closed
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

## Global-process MC: Xyce `.SAMPLING`

Xyce can consume the same PDK process distribution, but **not** through the
ngspice library scope.  The correct pattern is to declare the process variables
as `.global_param` *before* loading the `mc` library:

```spice
.global_param cnwvc=0.0
.global_param diff_cd=0.0
...
.lib "$PDK_ROOT/sky130A/libs.tech/ngspice/sky130.lib.spice" mc
.param mc_mm_switch=0
.param mc_pr_switch=1
.SAMPLING
+ param=cnwvc,diff_cd,...
+ type=normal,normal,...
+ means=0.0,0.0,...
+ std_deviations=1.0,1.5,...
.options SAMPLES numsamples=8 SAMPLE_TYPE=MC SEED=20261028
```

The ordering is essential.  The library's `critical.spice` defines `.param
lvn_threshold=0.0`, etc.; the preceding `.global_param` declarations make the
sampled values visible to the `montecarlo.spice` model expressions instead of a
library-local `.param` shadow.

Run it with mpirun as recommended:

```bash
make xyce-global-mc
# or
mpirun -np 1 ~/spack/opt/spack/*/xyce-*/bin/Xyce -b build/verify/xyce_global_mc/xyce_global.cir
```

`xyce_global_mc.py`:

- parses all 67 process `vary` entries from `critical.spice`;
- emits `.global_param` for each variable before `.lib mc`;
- samples them with the PDK standard deviations;
- reports common-mode and differential current for two identical devices;
- proves the seed changes the global draw (different seeds give different
  common-mode samples);
- uses `mpirun -np 1` by default and records the exact command.

8-sample result (Xyce 7.10, sky130A, `tt`, mpirun):

```text
common_std       = 8.04e-6 A
differential_std = 0.0 A
verdict          = global-process-supported
```

The two devices move together; that is the signature of a global-process draw,
not local mismatch.

## Why this is not just behavioral

The mismatch draw is inside the PDK device model expressions, not a series
gate-voltage offset.  Two instances with the same model see independent
`AGAUSS` draws.  The behavioral mode in `mc_runner.py` remains separate and is
never presented as PDK MC.

## Engine split

| Effect | Engine | Path |
|---|---|---|
| per-device local mismatch | ngspice | `pdk_mc.py --mode mismatch` |
| global process | Xyce | `xyce_global_mc.py --mode global` |
| both | separate runs; do not sum variances without a model | — |

Xyce local `AGAUSS` in this PDK/library did not produce a per-device difference
in the two-device probe, so local mismatch is kept on the verified ngspice
path.  Xyce is used for the global process, where its `.SAMPLING` and
`.global_param` semantics match the requirement.

The global Xyce run is transistor-level PDK MC (model expressions from
`montecarlo.spice`), not the behavioral offset injection in `mc_runner.py`.
No yield or silicon claim is made.
