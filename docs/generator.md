# Spec-driven digital IP generator

The generator is the entry point for product configurations.  It is not a
"run every PDK" script: it first filters architectures by capability, then
emits one self-contained digital package for the selected target.

## 1. Command

~~~bash
python3 scripts/gen_ip.py --spec spec/default.yaml --out out/default --force
python3 scripts/gen_ip.py --spec spec/default.yaml --check
python3 scripts/gen_ip.py --list-arch
~~~

The Makefile wrappers are:

~~~bash
make gen SPEC=spec/default.yaml OUT=out/default
make regress
make qualify REPORT=signoff.json LEVEL=PRE_PNR_TIMING
~~~

## 2. Specification

A spec is the single source of truth for one package:

~~~yaml
name: serdes_32x8_100m
target: link            # link | tx | rx
link:
  payload_width: 32
  words: 8
  line_rate_mbps: 100   # serial line rate (wire rate)
  coding: raw           # raw | 8b10b | 64b66b (width/align gated)
  bit_order: lsb_first
tx:
  architecture: auto    # parallel_mux | word_group_capture | word_streaming
  capture: auto         # parallel | word_group | word_streaming
rx:
  architecture: auto    # fixed_oversampling | adaptive_oversampling | source_sync
  oversample: auto
  align: comma_per_frame
  clocking: independent # independent | source_sync
  clock_source: independent # source_sync | independent | external_reference | ring_pll | adpll | dll
  clock_recovery: fixed_oversampling # none | source_sync | fixed_oversampling | adaptive_oversampling
  sampler_architecture: single_edge # single_edge | ddr | multiphase | interleaved
  ppm: 0
  sync_pattern: 0xBC
  sync_len: 32         # must equal payload_width for comma alignment
tech:
  tx: {name: icsprout55, scl: ics55_LLSC_H7CL}
  rx: {name: icsprout55, scl: ics55_LLSC_H7CL}
interface:
  preset: default       # minimal | default | soc | phy_ip
  enable_mode: static   # static | dynamic
  user_clock_mhz: 100
optimization:
  objectives: [area, power, latency]
~~~

Rules:

- `link.coding` accepts `raw`, `8b10b` and `64b66b`.  Coded links generate
  `serdes_8b10b_top` / `serdes_64b66b_top` (10-bit / 66-bit line words) and are
  width/align gated: 8b10b needs `payload_width: 8` and `align: external`
  or `comma_8b10b`; 64b66b needs `payload_width: 64` and `align: external`
  or `block_lock`.  They are **coding-layer packages, not complete PCS links**:
  8b10b ties `enc_ctrl` to 0 (no K-codes / comma emitted) and 64b66b lacks
  full IEEE control-field / lane-distribution semantics.  The exact limitations
  are recorded in `manifest.capabilities.coding.limitations` and the generated
  README.  `coding: none` and `interface.data: raw` are not real modes and are
  rejected.
- Comma alignment requires `rx.sync_len == link.payload_width`; a comma is a
  full word, not a byte embedded in a wider word.
- `interface.cdc: auto` is not inferred from equal clock numbers.  For a `soc`
  preset it resolves to `async_fifo` unless the spec supplies both
  `tx_user_clock_mhz` and `rx_user_clock_mhz` and sets
  `cdc_proven_synchronous: true`.  Explicit `cdc: none` is checked against the
  PHY clock on both sides.
- target link requires the same tech/scl for TX and RX.  Generate target tx and
  target rx separately for a cross-PDK link.
- fixed CDR requires OVERSAMPLE >= 4; OVERSAMPLE=2 requires allow_os2: true.
- adaptive CDR requires OVERSAMPLE >= 8 and is selected automatically when
  rx.ppm is non-zero.
- source_sync uses OVERSAMPLE=1 and CDR_BYPASS=1.
- `clock_source`, `clock_recovery` and `sampler_architecture` are independent
  spec axes, but the vocabulary is deliberately larger than the integrated
  backend.  **Accepted today**: `clock_source` = `independent` / `source_sync`
  (must equal `rx.clocking`); `clock_recovery` = `source_sync` /
  `fixed_oversampling` / `adaptive_oversampling` / `bang_bang` (must match the
  RX architecture; `none` only for `target: tx`); `sampler_architecture` =
  `single_edge`.  **Rejected (fail closed)**: `external_reference`, `ring_pll`,
  `adpll`, `dll`, `pi_cdr`, `ddr`, `multiphase`,
  `interleaved` -- these need physical phase clocks / verified sampler or PLL
  backends that do not exist yet.  Listing them in `CAPABILITY_CHOICES` is a
  roadmap, not a claim of support.  The DLL phase-select core is integrated as
  `CDR_MODE=4` at the RX top (`dll_backend.md` section 6), but the generator
  does not emit a mode-4 package yet, so `dll`/`multiphase` stay rejected
  here.
- The manifest records `clocking.sample_clock_mhz = line_rate_mbps ×
  OVERSAMPLE`; this is an RTL clock requirement, not proof that the target
  standard-cell flow closes at that frequency.
- The line-rate gate uses max_link_line_rate_mbps for target link and
  max_endpoint_line_rate_mbps for target tx/rx.  --relax-limits is for
  explicit experiments, not signoff.
- A second, independent gate bounds the **RX sample clock**:
  `sample_clock_mhz = line_rate_mbps x OVERSAMPLE` must not exceed
  `limits.max_sample_clock_mhz` for `target: link`/`rx` with oversampling.
  The four profiles declare 200 / 50 / 200 / 400 MHz (sky130 / gf180mcuD /
  ihp-sg13g2 / icsprout55), taken from the pre-PnR results in
  `docs/prepnr_sta.md` -- e.g. 400 MHz on 130 nm is not realistic for this
  RTL, while 2.5 ns (400 MHz) is demonstrated on icsprout55 H7CL.  Both the
  measured clock and its limit are recorded in `manifest.clocking`.
  `--relax-limits` downgrades this to a warning.
- A third tier distinguishes **"can generate" from "has a verified
  closure"**.  `limits.clock_evidence_by_arch.<arch>` records four sample-clock
  values per PDK:
  - `tested_max_mhz` -- the highest clock actually run (not necessarily
    passing);
  - `timing_closed_mhz` -- a post-route run at that period with WNS >= 0;
  - `physical_signoff_mhz` -- timing closed **and** DRC/LVS clean;
  - `cp_implied_mhz` -- `1000 / critical-path`, an estimate, never a closure.

  Only sky130 has a verified closure today (45.45 MHz, 22 ns run, DRC/LVS 0;
  the 25 ns run also closes at 40 MHz).  gf180mcuD, ihp-sg13g2 and icsprout55
  have **no closing post-route period**; the former 30/80/65 MHz scalars were
  CP-implied estimates, and ihp/icsprout are not DRC/LVS clean.  The generator
  records `manifest.clocking.evidence` with the tier, source, corner, tool and
  commit, and appends one of:
  - above `physical_signoff_mhz`: "unverified experiment";
  - inside `timing_closed_mhz` but above physical signoff: "timing evidence,
    not physical signoff";
  - no timing closure: "no verified post-route timing closure; CP-implied X is
    an estimate".

  It still does not fail by default: the gate says which evidence exists, not
  that the experiment is forbidden.  The legacy scalar
  `signoff_sample_clock_mhz_by_arch` is read as a fallback with
  `physical_signoff_mhz: null`.  Note that `spec/phy_ip.yaml` was corrected
  from 100 Mb/s / OS=8 (800 MHz, unclosable) to 50 Mb/s / OS=8 (400 MHz) when
  the second-tier gate was added.

  `constraints.require_clock_evidence: none | timing_closed |
  physical_signoff` (default `none`) turns the warning into a fail-closed
  generation gate.  For example, a physical-signoff deliverable on gf180mcuD at
  30 MHz is rejected because it has no closing post-route run, only a
  CP-implied estimate; on sky130 at 40 MHz it passes, and at 50 MHz it fails.
  The requirement is recorded in `manifest.clocking.evidence.required` and
  `.satisfied`.  It only applies to `target: link` or `rx`; asking for it on a
  TX-only spec is a spec error rather than silent no-op.
- `constraints` also accepts `io_delay_ns` (0.200), `output_load_pf` (0.010),
  `sync_stages` (3), `lock_thresh` (8), `lock_stale_bits` (16) and
  `fifo_depth` (16, must be a power of two).  These used to be hard-coded;
  defaults are unchanged.
- `capabilities` is an optional mapping.  A requested standalone or
  unimplemented block is rejected; use the explicit `none` bypass.  See
  `docs/digital_training_crc.md`.
- interface.data: axi4_stream is supported for target tx/rx, generating
  serdes_tx_axis_digital / serdes_rx_axis_digital.  For target link, preset soc
  + data axi4_stream + control apb + cdc none generates the single-clock
  serdes_soc_wrapper (comma + payload framing).  cdc async_fifo and multi-clock
  APB/AXIS are still scaffold.

Example specs: spec/tx_axis.yaml, spec/rx_axis.yaml, spec/soc_single_clock.yaml.

## 3. Architecture selection

Candidates and relative metrics live in tech/arch_db.yaml.  Each candidate may
carry `tech_overrides` for a PDK; the generator merges the override (max line
rate, and optionally metrics) before filtering and scoring.  The generator:

1. applies the PDK override for the selected tech;
2. filters by target, RATIO, line rate, oversample and clocking;
3. computes the Pareto frontier over area/power/latency;
4. restricts selection to the frontier, then picks the lowest weighted score
   for the requested objectives (unless `tx/rx.architecture` was explicit).

Metrics are no longer hand-written scalars in `arch_db.yaml`.
`scripts/ppa_model.py` computes RATIO- and OVERSAMPLE-dependent area and
clock-load metrics from a linear cell-count model fitted to Yosys
generic-gate measurements at 8x4 and 32x8; see [`ppa_model.md`](ppa_model.md).
Precedence is measured `ppa_measured.yaml` > explicit
`tech_overrides.<pdk>.metrics` > the model.  These are capability-gate
estimates, **not** PDK area, measured power or signoff PPA.  The selected
candidate's `metrics`, `metrics_source`, `ppa_model.estimate`,
`confidence: modeled_yosys_cells` and
`confidence_scope: capability_gate_estimate_not_signoff` are recorded in
manifest.json, together with a top-level `manifest.ppa_model` block.
`tech.pdk_paths_resolved` also requires the expanded Liberty files to exist;
`tech.pdk_lib_files_present` records that check separately.

### Measured PPA database

`tech/ppa_measured.yaml` is the measured override database.  Import a CSV with:

```bash
make ppa-import PPA_CSV=measured.csv PPA_OUT=tech/ppa_measured.yaml
```

CSV columns:

```text
tech,scl,kind,arch,max_line_rate_mbps,rel_area,rel_power,rel_latency,confidence,source,area_um2,power_mw,provenance_report,provenance_sha256,corner,period_ns,git_commit,tool
```

A `measured_post_route` / `measured_pre_pnr` claim must carry a
`collect_power.py` evidence record, its sha256, a corner and a period; the
importer verifies all four and rejects the row otherwise.  `--allow-unproven`
downgrades such a row to `unverified_claim` instead.  `gen_ip.py` repeats the
check when loading a `ppa_db`, so a hand-written YAML cannot claim measured
PPA.  See [`power_flow.md`](power_flow.md).

```bash
make ppa-power-import POWER_RUN=<librelane-run-dir> \
  PPA_TECH=sky130 PPA_SCL=sky130_fd_sc_hd \
  PPA_KIND=rx PPA_ARCH=source_sync PPA_RATE=100 \
  PPA_CSV=build/ppa/power.csv PPA_OUT=tech/ppa_measured.yaml
```

`rel_*` are normalized within one tech/period/signoff setup.  `area_um2` and
`power_mw` are optional traceability fields and are not mixed into the relative
score.  `scripts/gen_ip.py --ppa-db <path>` applies the entries before
capability filtering; `manifest.json` records `ppa_source`, `confidence` and
absolute values.  A measured entry with a lower line-rate gate will reject a
spec that the relative database would have allowed.

### Optimization controls

```yaml
optimization:
  objectives: [area, power, latency]
  weights: {area: 1.0, power: 3.0, latency: 1.0}
  budgets: {area: 1.2, power: 1.5}
  aggregation: weighted_sum   # weighted_sum | minmax | lexicographic
```

- `weights` must use keys from `objectives`, be >= 0, and give at least one
  objective a positive weight.  Unlike the old equal-weight code, weights now
  actually change the winner.
- `budgets` are **hard filters** on the normalised metrics.  A candidate over
  budget is removed before scoring; the rejected names and reasons are recorded
  in `manifest.architecture.<side>.selection.budget_rejections`.
- `aggregation`: `weighted_sum` (default), `minmax` (minimise the worst
  weighted metric), or `lexicographic` (compare metrics in descending weight
  order).  The chosen score, weights, budgets and aggregation are all recorded
  in `manifest.architecture.<side>.selection`; `manifest.optimization` repeats
  the spec-level settings.
- **Pareto note:** for any monotone score (all weights >= 0), a dominated
  candidate can never win, so the frontier filter is informational rather than
  decisive.  Budgets and weights are the effective levers.  This is stated in
  `selection.note` so the frontier is not overclaimed.
- `rx.clocking: auto` resolves to `source_sync` when
  `optimization.clock_power_priority: true` and `rx.ppm == 0` (an external
  forwarded clock is required); otherwise it stays `independent`.
  `rx.oversample: auto` then resolves to 2 when `allow_os2: true`, else 4 (or
  8 when ppm != 0).  `spec/low_power.yaml` is the reference config and drops
  the RX clock from 4x to 1x.
- Whenever the selected RX still uses oversampling, the manifest records
  `clocking.recommendation`: the lowest-clock alternative, its
  `alternative_clock_load_ratio` from `ppa_model.py`, and the requirement
  (forwarded clock, or `allow_os2`).  It is advisory only -- the generator
  never silently changes clocking.
- `spec/power_tradeoff.yaml` demonstrates a two-candidate frontier at 32x8
  (`word_group_capture` vs `word_streaming`) with a budget that rejects the
  larger one.  A restricted `fixed_oversampling_os2` RX candidate is reachable
  with `rx.oversample: 2` + `rx.allow_os2: true` and has ~0.5x the clock load
  of OS=4.

Current candidates:

| Subsystem | Candidate | Notes |
|---|---|---|
| TX | parallel_mux | single-cycle capture; RATIO <= 128 |
| TX | word_group_capture | one word per cycle internally; external bus must be held for WORDS cycles |
| TX | word_streaming | true per-word valid/ready; narrow input; endpoint only |
| RX | fixed_oversampling | frequency-locked clocks |
| RX | adaptive_oversampling | first-order ppm tracking; OS >= 8 |
| RX | source_sync | forwarded bit clock; no CDR acquisition |

## 4. Interface presets

| Preset | Data | Control | CDC | Status |
|---|---|---|---|---|
| minimal | valid/ready parallel | native | none | complete |
| default | valid/ready parallel | native | auto | complete |
| soc | AXI4-Stream | APB | none or async FIFO | serdes_soc_wrapper / serdes_soc_cdc_wrapper available (source-sync link) |
| phy_ip | valid/ready + optional blocks | APB | async FIFO + BIST/calibration/DFT | APB/FIFO primitives exist; BIST/DFT/top integration is scaffold |

soc now has two real wrappers: `serdes_soc_wrapper` for a single clock and
`serdes_soc_cdc_wrapper` for separate pclk/AXIS/tx/rx clocks with async FIFOs
and 2FF control/status synchronization.  Both use a source-synchronous link
(OVERSAMPLE=1) and comma+payload framing; fixed/adaptive CDR in a SoC wrapper
is still open.  Both wrappers integrate the pad-level PRBS BIST behind APB: BIST_CTRL
(enable/start/clear/inject/loopback/snapshot) at register 8, coherent counter
snapshots at 10/11 and BIST_STATUS at 12.  The single-clock wrapper also
supports the word/datapath BIST (BIST_CTRL[6]), which drives an AXIS PRBS frame
source through the serializer and checks the RX AXIS frames; its counters are
at registers 13/14.  `phy_ip` remains a scaffold until
calibration/DFT integration exists.  `target: tx`/`rx` with
`interface.data: axi4_stream` generates a real endpoint package.  The presets
fail fast unless --allow-incomplete is passed.  A scaffold package now records
`manifest.missing_blocks` + `features_missing`, emits a warning, and adds a
"Missing blocks" section to its README.  `make gen` exposes extra flags via
`GEN_FLAGS` (e.g. `make gen ... GEN_FLAGS=--allow-incomplete`).  The generated manifest starts with
`link_capability.evidence_level = NONE` and
`rtl_link_smoke_pass = false`; `make verify-link VERIFY_PACKAGE=<out>` runs the
bound SI/BER gate and only then records a validated rate, profile hash and the
`RTL_LINK_SMOKE` evidence block.  The generator itself never invents a
link-rate claim.  The generated soc packages include
`serdes_bist.sv`, `bist_snapshot.sv` and the word BIST primitives.

## 5. Package layout

~~~text
out/<name>/
  rtl/                  selected native SV sources + filelist.f + params.svh/json
  rtl/sv2v/             sv2v-converted Verilog used by LibreLane/Yosys
verification_config   manifest: CDR mode, OS, align/frame, clock periods, source
  rtl/filelist_sv2v.f   converted-source file list
  constraints/
    pre_pnr.sdc         async reset excluded; pre-CTS
    post_cts.sdc        propagated clock; reset recovery enabled
    signoff.sdc         propagated clock + tech design-rule limits; reset recovery enabled
  pnr/librelane.yaml    LibreLane configuration
  pnr/README.md         clock/CTS caveats
  manifest.json         spec hash, git commit, params, architecture, qualification
  qualification.json    starts UNVERIFIED
  README.md             generated datasheet/integration notes
~~~

The package copies the needed RTL; it is self-contained for synthesis and PnR.
Native SystemVerilog is retained for simulation, lint and generated-package
compile smoke tests.  `pnr/librelane.yaml` points at the sv2v-converted files,
so the LibreLane Yosys frontend never sees packed arrays, `always_comb` or other
newer SV constructs.  Generation fails closed if sv2v is missing; the explicit
`--allow-native-sv` escape hatch emits native files for experiments only.
It does not include the analog TX/RX wrapper, PEX, SI/PI, jitter/BER or silicon
data.

## 6. SDC stages

The generator emits three SDC files instead of one:

- pre_pnr: reset recovery/removal excluded; used for PnR and early timing.
- post_cts: set_propagated_clock; reset recovery/removal enabled.
- signoff: post-CTS plus `set_max_fanout` / `set_max_transition` /
  `set_max_capacitance` from `tech/<pdk>/tech.yaml` (cts:).  It is still a
  template: OCV derates, derated corners and extracted parasitics are explicitly
  NOT emitted and must be supplied by the PDK signoff flow.  The emitted file
  says so.

Static enables may be false-pathed.  Dynamic enables must not be; set
interface.enable_mode: dynamic for a real streaming/backpressure interface.

## 7. PnR clock declaration

`CLOCK_PORT` is emitted as a list of every clock in the design (for
`target: link`, `[tx_clk, rx_clk]`; for the SoC CDC wrapper all five clocks).
LibreLane's CTS step uses that list as `CLOCK_NET`, so no manual "add rx_clk to
the clock set" step remains.  `CLOCK_PERIOD` stays a scalar for the primary
clock; the per-clock periods are defined by the supplied SDC files.  This is
recorded in `pnr/README.md` and `manifest.clocking.note`.
