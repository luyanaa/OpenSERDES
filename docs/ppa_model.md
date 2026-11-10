# PPA model (item 1 of the PPA / architecture audit)

## Why this exists

`tech/arch_db.yaml` used to carry a single hand-written relative metric triple
per candidate.  Synthesis contradicted it:

| Candidate | old `area` | measured cells ratio (8x4) |
|---|---:|---:|
| `word_streaming` | 0.80 (20% smaller) | **2.83x larger** |
| `word_group_capture` | 1.05 | 1.30x |
| `adaptive_oversampling` | 1.25 | **3.89x** |
| `source_sync` | 0.70 | 0.55x |

The `power` column was worse: it did not model the oversampling multiplier at
all, so an 8x CDR looked 1.20x instead of ~3.4x the clock load of 4x.

## Model

`scripts/ppa_model.py` fits a linear cell-count model to two measured Yosys
generic-gate points per architecture:

```text
cells(RATIO) = cells_base + cells_per_ratio_bit x RATIO
dff(RATIO)   = dff_base   + dff_per_ratio_bit   x RATIO
clock_load   = dff x f_clock
f_clock(TX)  = line_rate_mbps
f_clock(RX)  = line_rate_mbps x OVERSAMPLE
```

Measurements (sky130 generic `yosys synth; stat`, 25 Mb/s, parameters taken
from the generated `pnr/librelane.yaml` `SYNTH_PARAMETERS`, not RTL defaults):

| Architecture | cells/DFF @8x4 | cells/DFF @32x8 | base | per-bit |
|---|---|---|---:|---:|
| `parallel_mux` | 109 / 41 | 582 / 268 | 41.4 | 2.112 |
| `word_group_capture` | 142 / 46 | 626 / 277 | 72.8 | 2.161 |
| `word_streaming` | 308 / 44 | 1977 / 272 | 69.6 | 7.451 |
| `fixed_oversampling` OS=4 | 218 / 67 | 758 / 319 | 140.8 | 2.411 |
| `fixed_oversampling` OS=8 | 228 / 68 | 767 / 320 | (same model) | |
| `adaptive_oversampling` OS=8 | 847 / 115 | 1382 / 367 | 770.7 | 2.384 |
| `source_sync` | 120 / 50 | 656 / 302 | 43.4 | 2.393 |

Normalisation: TX against `parallel_mux`, RX against `fixed_oversampling`
OS=4, all at the same RATIO and line rate.  `latency_factor` comes from the
documented handshake behaviour (1.00 / 1.25 / 1.25 for TX, 1.00 / 1.10 /
0.75 for RX), not from STA.

## Precedence in `select_arch()`

1. measured entry from `--ppa-db` / `tech/ppa_measured.yaml` (`metrics_source:
   measured_ppa_db`)
2. explicit `tech_overrides.<pdk>.metrics` (`tech_override`)
3. `ppa_model` computed metrics (`ppa_model`, default)

`manifest.architecture.<side>` now records `metrics`, `metrics_source`,
`ppa_model.estimate`, `confidence: modeled_yosys_cells` and the existing
`confidence_scope: capability_gate_estimate_not_signoff`.  A top-level
`manifest.ppa_model` block states what is explicitly **not** measured.

## Limits (do not overclaim)

- `cells` are Yosys **generic gates**; no `dfflibmap`/ABC mapping, no
  placement, no routing, no PDK area.  Ranking only.
- `rel_power` is a **clock-load proxy**, `DFF x f_clock`.  It excludes
  combinational switching, glitching, leakage, clock-tree capacitance and I/O.
  It is the right first-order quantity for comparing oversampling factors and
  is **not** a power measurement.
- `rel_latency` is documented handshake behaviour, not STA.
- The fit is exact only at RATIO 32 and 256; interpolation/extrapolation is
  linear and should be re-fitted when new architectures or RATIOs are measured.
- Nothing here is `POST_ROUTE_TIMING`, `PHYSICAL_SIGNOFF` or measured PPA.

## Reproduce

```bash
python3 -m unittest verification.tests.test_ppa_model -v
```

To re-measure, generate a package for each architecture, then synthesise with
the generated `SYNTH_PARAMETERS` applied as `hierarchy -chparam`:
```bash
yosys -p "read_verilog <files>; hierarchy -top <design> -chparam K V ...; \
          synth -top <design>; stat"
```
