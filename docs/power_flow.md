# Power evidence flow (item 3 of the PPA audit)

## Problem

Before this change, `tech/ppa_measured.example.yaml` was empty and
`build/ppa/*.yaml` were regression fixtures with `source: run-001` and
`confidence: measured_post_route`.  `scripts/import_ppa.py` copied the CSV
`confidence` verbatim and `gen_ip.py` copied it into the manifest, so a
hand-written CSV could make a generated IP package claim **measured post-route
PPA** without any measurement.  There was no power analysis anywhere in the
flow: `verification/analysis/` only has eye / bathtub / BER / JTOL, and no
`report_power`, SAIF, VCD or switching-activity annotation existed.

## Chain

```text
LibreLane STAPostPNR (openroad/sta/corner.tcl: report_power)
  -> <run>/final/metrics.json : power__internal/switching/leakage/total
  -> scripts/collect_power.py : hash-verifiable evidence record
  -> scripts/import_ppa.py    : verify provenance, emit ppa_measured.yaml
  -> scripts/gen_ip.py        : re-verify at load, record in manifest
```

### 1. Collect (fail-closed)

```bash
python3 scripts/collect_power.py \
  --run build/analog_pnr_sky130/runs/analog_link_signoff \
  --out build/verify/power_evidence.json \
  --csv-out build/ppa/power.csv \
  --tech sky130 --scl sky130_fd_sc_hd --kind rx --arch source_sync \
  --rate 100 --source analog_link_signoff
```

The collector refuses to produce a record if `final/metrics.json` is missing
or lacks any of the four `power__*` keys, and records `metrics_sha256`,
`corner`, `period_ns`, `git_commit`, `tool` and `activity_source`.

Verified in this checkout on the completed soft-stdcell sky130 run:

```text
power__total = 4.592618 mW  corner=nom_tt_025C_1v80  period=20.0 ns
internal 3.402 mW / switching 1.191 mW / leakage 0.054 uW
```

### 2. Import (provenance gate)

`import_ppa.py` requires, for every `measured_*` row: `source`, `corner`,
`period_ns`, `provenance_report` and `provenance_sha256`.  It verifies that
the report file hashes to `provenance_sha256`, that it is a
`librelane-power-evidence` record with power data, and that corner/period
agree.  Failure is an error; `--allow-unproven` instead downgrades the row to
`confidence: unverified_claim`.

### 3. Load (second check)

`gen_ip.py load_ppa()` downgrades any `measured_*` entry whose
`provenance.verified` is not true (i.e. a hand-written YAML that bypassed the
importer) to `unverified_claim`, emits a warning and records
`provenance_warning` in `manifest.architecture.<side>`.  A verified entry
carries `ppa_provenance` with the report hash and activity source.

`make ppa-power-import` runs steps 1 + 2 in one command.

## What this does NOT close

- **`make sim` still does not annotate a PnR run.**  `collect_power.py`
  refuses to turn default OpenSTA activity into `energy_j_per_bit`.  E_bit
  = P_active / R_useful is written only for a named workload (`prbs`,
  `constant`, `alternating`, `throughput`, `idle`) with a SAIF or VCD file
  and an annotation coverage in [0, 1].  `R_useful` is the payload rate
  after training, coding and framing.  `idle` has no payload, so its rate
  is zero and E_bit stays null.
- **Half-rate CTS power is not a workload energy.**
  `scripts/cts_power.py` (`make cts-power`) places `serializer_half_rate`
  (4x8) and runs `clock_tree_synthesis`.  The recorded CTS number is
  `report_power` in that OpenROAD session after
  `estimate_parasitics -placement`.  Activity is the OpenSTA default: the
  new buffers are not in a pre-CTS VCD, so there is no annotation coverage
  and `energy_j_per_bit` is null.  Analog stays null.  Placement wire
  capacitance is not routed SPEF.  See `docs/timing_optimization.md` §4.4.
- **Pre-route workload annotation is a separate script.**
  `scripts/annotate_activity.py` maps `serializer` (4x8, capture mode 0),
  simulates the gate netlist with `-DFUNCTIONAL` (Yosys does not tie the
  sky130 power pins, and the timing models then drive X), dumps every cell
  pin, and reads that VCD in OpenSTA.  ABC is told not to use `lpflow*`
  cells: they mapped `ready` to an isolation cell and the frame never
  started.  Coverage, power groups and the measured payload rate are
  written to `build/activity/serializer.json`.  The Clock group is recorded
  when OpenSTA prints it; with no CTS it is zero, which is a real report
  value, not a missing bucket.  Analog stays null.  This is ideal-clock,
  no-SPEF power.  It does not replace a post-route `collect_power.py`
  record and must not be compared with one.
- The 4.59 mW point is for the **soft-stdcell analog link** top
  (`serdes_analog_link`), not for an individual generated digital TX/RX
  endpoint package.  It is a flow demonstration, not a per-architecture PPA
  number.  Do not feed it into `select_arch()` as if it were the source_sync
  RX area/power.
- No per-corner / per-PDK power sweep, no leakage-vs-Vt characterisation, no
  silicon correlation.  `rel_power` in `ppa_model.py` remains a clock-load
  proxy until measured per-architecture entries exist.
- Power is not yet a qualification level in `scripts/qualify.py`; a
  `measured_post_route` PPA entry is provenance-verified but is not a link
  or silicon validation.

## Reproduce

```bash
python3 -m unittest verification.tests.test_power_flow -v
python3 scripts/regress.py   # includes ppa_unproven_rejected / _downgraded
```
