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
  coding: raw           # raw | 8b10b | 64b66b
  bit_order: lsb_first
tx:
  architecture: auto    # parallel_mux | word_group_capture | word_streaming
  capture: auto         # parallel | word_group | word_streaming
rx:
  architecture: auto    # fixed_oversampling | adaptive_oversampling | source_sync
  oversample: auto
  align: comma_per_frame
  clocking: independent # independent | source_sync
  ppm: 0
  sync_pattern: 0xBC
  sync_len: 8
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

- target link requires the same tech/scl for TX and RX.  Generate target tx and
  target rx separately for a cross-PDK link.
- fixed CDR requires OVERSAMPLE >= 4; OVERSAMPLE=2 requires allow_os2: true.
- adaptive CDR requires OVERSAMPLE >= 8 and is selected automatically when
  rx.ppm is non-zero.
- source_sync uses OVERSAMPLE=1 and CDR_BYPASS=1.
- The line-rate gate uses max_link_line_rate_mbps for target link and
  max_endpoint_line_rate_mbps for target tx/rx.  --relax-limits is for
  explicit experiments, not signoff.

## 3. Architecture selection

Candidates and relative metrics live in tech/arch_db.yaml.  Each candidate may
carry `tech_overrides` for a PDK; the generator merges the override (max line
rate, and optionally metrics) before filtering and scoring.  The generator:

1. applies the PDK override for the selected tech;
2. filters by target, RATIO, line rate, oversample and clocking;
3. computes the Pareto frontier over area/power/latency;
4. selects the lowest weighted score for the requested objectives.

The metrics are relative and partly estimated.  They are capability gates, not
signoff PPA.  Replace them with measured post-route data as the qualification
database fills in.  The selected candidate, its tech override and confidence are
recorded in manifest.json.

### Measured PPA database

`tech/ppa_measured.yaml` is the measured override database.  Import a CSV with:

```bash
make ppa-import PPA_CSV=measured.csv PPA_OUT=tech/ppa_measured.yaml
```

CSV columns:

```text
tech,scl,kind,arch,max_line_rate_mbps,rel_area,rel_power,rel_latency,confidence,source,area_um2,power_mw
```

`rel_*` are normalized within one tech/period/signoff setup.  `area_um2` and
`power_mw` are optional traceability fields and are not mixed into the relative
score.  `scripts/gen_ip.py --ppa-db <path>` applies the entries before
capability filtering; `manifest.json` records `ppa_source`, `confidence` and
absolute values.  A measured entry with a lower line-rate gate will reject a
spec that the relative database would have allowed.

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
| soc | AXI4-Stream | APB | async FIFO | APB/FIFO primitives exist; AXI/CDC/top integration is scaffold |
| phy_ip | valid/ready + optional blocks | APB | async FIFO + BIST/calibration/DFT | APB/FIFO primitives exist; BIST/DFT/top integration is scaffold |

soc and phy_ip still require the AXI4-Stream/CDC/top integration wrappers and
BIST/DFT blocks that are not in this release.  APB CSR (`rtl/apb_csr.sv`) and
the async FIFO (`rtl/async_fifo.sv`) primitives are implemented and tested, but
they are not yet wired into a generated top.  The presets fail fast unless
--allow-incomplete is passed, and the generated package explicitly records the
missing blocks.

## 5. Package layout

~~~text
out/<name>/
  rtl/                  selected RTL sources + filelist.f + params.svh/json
  constraints/
    pre_pnr.sdc         async reset excluded; pre-CTS
    post_cts.sdc        propagated clock; reset recovery enabled
    signoff.sdc         signoff template; reset recovery enabled
  pnr/librelane.yaml    LibreLane configuration
  pnr/README.md         clock/CTS caveats
  manifest.json         spec hash, git commit, params, architecture, qualification
  qualification.json    starts UNVERIFIED
  README.md             generated datasheet/integration notes
~~~

The package copies the needed RTL; it is self-contained for synthesis and PnR.
It does not include the analog TX/RX wrapper, PEX, SI/PI, jitter/BER or silicon
data.

## 6. SDC stages

The generator emits three SDC files instead of one:

- pre_pnr: reset recovery/removal excluded; used for PnR and early timing.
- post_cts: set_propagated_clock; reset recovery/removal enabled.
- signoff: post-CTS plus signoff template; reset recovery/removal enabled.

Static enables may be false-pathed.  Dynamic enables must not be; set
interface.enable_mode: dynamic for a real streaming/backpressure interface.

## 7. Current limitation

For target link the PnR config still has a scalar CLOCK_PORT (tx_clk).  The SDC
creates both tx_clk and rx_clk.  Confirm that the CTS step adds rx_clk to its
clock set; this is recorded in pnr/README.md and manifest.json.
