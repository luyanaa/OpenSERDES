# sky130 stdcell inventory and the 9-track mixed library

The installed `sky130A` view ships only `sky130_fd_sc_hd` (and the 5 V
`hvl`).  `/grid/sky130` carries more source families; this note records what is
actually usable, which families pass cell-level DRC/LVS, and the ics55-style
mixed-library experiment built on top of them.

Tools: `scripts/library_drc_lvs.py` (Magic `drc(full)` + Netgen per cell),
`scripts/sky_json2lib.py` (skywater `*.lib.json` → Liberty), and
`scripts/sky_install_family.py` (builds the `libs.ref`/`libs.tech` view, single
family or mixed).

## 1. Inventory

`/grid/sky130` source trees carry per-cell GDS, LEF (`*.lef` abstract and
`*.magic.lef`), SPICE, CDL and per-corner `*.lib.json` timing, but not the
`libs.ref` layout LibreLane reads.  Cell height decides what can be mixed:
rows only tile when every cell in the row has the same height.

| family | cells (LEF macros) | cell height | Liberty corners per cell | GDS | CDL/SPICE | Magic `.mag` | mixable with |
|---|---|---|---|---|---|---|---|
| `sky130_fd_sc_hd` (installed) | 437 | 2.72 µm | 18 files (TT/SS/FF ×V) | merged 1 | merged 1 | 891 | baseline |
| `sky130_fd_sc_hdll` | 332 | **2.72 µm** | TT, SS, FF (`1v56/1v65/1v76`), no `1v95` | 334 | 332 | no | **hd / as_sc_hs** |
| `sky130_as_sc_hs` | 74 (85 layouts) | **2.72 µm** | TT, SS(`055C_1v70`), FF(`005C_1v90`) | 75 | no | 85 | **hd / hdll** |
| `sky130_fd_sc_ms` | 391 | 3.33 µm | SS complete; TT/FF only as variants | 391 | 391 | no | lp / ls |
| `sky130_fd_sc_ls` | ~380 | 3.33 µm | TT, SS, FF | 399 | 399 | no | ms / lp |
| `sky130_fd_sc_lp` | ~300 | 3.33 µm | SS/FF only (no TT) | 740 | 740 | no | ms / ls |
| `sky130_osu_sc_t12/t15/t18` | 66–75 each | 4.44 / 5.55 / 6.66 µm | TT/SS/FF + voltage corners | 216–218 | 216–218 | yes | nothing here |
| `sky130_fd_sc_hvl` (installed) | 69 | 4.07 µm | 61 files (5 V) | merged 1 | merged 1 | 140 | nothing (5 V only) |
| `sky130RHBDlib` | 82 | 7.76 µm | 29 files | 253 | yes | 347 | nothing (rad-hard, tall) |
| `sky130_fd_sc_hs` | **0** | — | — | — | — | — | **unavailable** |

`sky130_fd_sc_hs` has only empty version directories in
`~/.ciel/.../skywater-pdk/libraries/` and no `libs.ref` view — the earlier
"hs is 1.1 ns faster" note was TT-only and is not buildable here.

Two families claim the same 2.72 µm (9-track) height as `hd`:
`hdll` (low leakage) and the custom `as_sc_hs` (high-speed).  That is the
precondition for the ics55-style mix.

## 2. Cell-level DRC / LVS

Method (`scripts/library_drc_lvs.py`): Magic loads each layout with the sky130A
tech, `drc style drc(full)`, `expand`, `drc catchup`, `drc listall why`; Netgen
compares the Magic extraction against the family's per-cell SPICE with
`sky130A_setup.tcl`.

Two rules that look like violations are row-level, not cell-level: `LU.2`/`LU.3`
(latch-up tap distance) and `nwell.4` (nwell tap connection) cannot be satisfied
by a lone stdcell — taps come from `tapvpwrvgnd` cells in the placed row.  They
are counted and reported separately (`drc_tap_rules`) and the routed-design
Magic DRC covers them.

| family | cells checked | DRC pass | LVS match uniquely | device-equivalent, pin match blocked | physical-only (no devices) | verdict |
|---|---:|---:|---:|---:|---:|---|
| `sky130_fd_sc_hd` (control) | 4 (sample) | 4 | 4 | 0 | 0 | harness sanity check |
| `sky130_as_sc_hs` | 85 | **85** | — (no CDL/SPICE shipped) | — | — | DRC-clean, cell LVS unprovable |
| `sky130_fd_sc_hdll` | 334 | **334** | 274 | 51 | 8 | DRC-clean; every device netlist equivalent (`muxb` template cell has no golden netlist) |
| `sky130_fd_sc_ms` | 391 | **391** | **375** | 2 | 14 | DRC-clean; every device netlist equivalent |
| `sky130_fd_sc_ls` | 399 | **399** | **381** | 4 | 14 | DRC-clean; every device netlist equivalent |
| `sky130_fd_sc_lp` | in flight | | | | | |

Notes on the LVS column:

- Netgen matches top-level pins by position.  The skywater per-cell SPICE and
  the Magic extraction list the same ports in a different order, so
  `scripts/library_drc_lvs.py` rewrites the golden `.subckt` port list into the
  extracted order before comparing.  Cells whose GDS carries no pin labels
  still lose those ports at extraction (they become internal nodes); for those
  the report is `Device classes … are equivalent` with a pin-matching failure.
  That is an extraction-label artifact, not a connectivity difference.
- `fill`/`tap`/`latchup` cells have no transistors, so there is nothing for
  Netgen to compare; they are counted as physical-only.
- `conb` (tie) and `diode` are device-carrying but their top-level port pairing
  is not resolvable by Netgen (power/analog pins); device classes are
  equivalent.
- **Leakage data defect found:** four `hdll` cells have implausible
  `cell_leakage_power` in the upstream JSON, the worst being
  `sky130_fd_sc_hdll__dfrtp_4` at **9.35 nW** against 0.021 nW for `dfrtp_2`
  (x442; the `hd` `dfrtp_4` is 0.0108 nW).  `a211oi_4`, `clkinvlp_4` and
  `and4bb_4` are 31-84x off the same way.  `ms`/`ls`/`lp` have no such outlier.
  Any leakage-driven optimisation over `hdll` must exclude or patch these four
  cells, otherwise the tool sees a 580x penalty on the 4x DFF and refuses to
  upsize.
- **Netlist naming defect found:** the skywater-pdk CDLs name device models
  unprefixed (`nfet_01v8`), while Magic extraction and the open_pdks CDL use
  `sky130_fd_pr__nfet_01v8`.  Netgen then matches no devices and reports
  "circuit contains no devices" for every such cell, which shows up as a
  top-level device-count mismatch (1530 layout vs 1262 schematic).  The
  installer rewrites the model names in the merged CDL.
- **Library defect found:** the per-cell LEFs of `hdll`/`ms`/`ls`/`lp` declare
  `VPWR` and `VGND` but omit `VNB` and `VPB`, while their CDL/SPICE carry both.
  A design that mixes such a family with `hd` therefore fails top-level LVS
  with a pin-list mismatch and a net-count difference.  The installer now
  patches the missing well-bias pins into the merged LEF, borrowing the
  geometry from the same-name `hd` cell (identical `unithd` site) and
  synthesising it from the cell width for the 43 cells `hd` does not have.

`as_sc_hs` ships GDS/LEF/Liberty/Magic but no CDL or SPICE, so a Netgen golden
does not exist; connectivity for it can only be proven on a routed design (the
flow's own Netgen step).  The vendor `hdll`/`ms` trees do ship per-cell SPICE and
CDL, so both gates apply.

## 3. The mixed 9-track library (`sky130_mix9t`)

ics55 exposes H7CL/H7CH/H7CR as three 7-track libraries and lets the flow pick
per cell.  The sky130 equivalent merges `hd` (density) and `hdll`
(low leakage), both 2.72 µm:

- **Liberty**: one library per corner, 752 cells (428 hd + 324 hdll).  `hdll`
  timing comes from the source `*.lib.json`, converted with
  `scripts/sky_json2lib.py`; the converter was validated against the installed
  open_pdks `hd` Liberty — identical numeric content for `inv_1` (417/417
  values), `nand2_1` (893/893), `dfxtp_1` (600/600) and `a2111o_1` (2421/2421).
- **LEF/tech LEF**: merged abstract LEF (777 macros) over the same `unithd`
  site and `hd` metal stack; `nom`/`min`/`max` tech LEFs are derived by
  applying the `hd` corner RC ratios to the family nominal tech LEF.
- **GDS/CDL/SPICE**: per-cell GDS for both families, merged CDL/SPICE for
  extraction and LVS.
- **Flow cells**: tap/decap/fill/diode/tie/clock-buffer cells are taken from
  `hd` explicitly, so row structure is identical to the baseline; the SCL
  config maps them by full cell name (a merged library keeps the families' own
  cell names).
- **Corners**: `nom_tt_025C_1v80` and `nom_ss_100C_1v60`.  The source trees do
  not ship a plain per-cell FF Liberty at the PDK's `ff_n40C_1v95` corner
  (only `_ccsnoise`), and the PDK pad-library table is keyed on those corner
  names, so the experiment covers TT + SS.  Setup closure is governed by SS.

Install:

```bash
make sky-lib-install            # builds + symlinks libs.ref/sky130_mix9t
python3 scripts/sky_install_family.py --scl sky130_fd_sc_hdll \
    --family sky130_fd_sc_hdll=/grid/sky130/sky130_fd_sc_hdll --install
```

`tech/sky130/tech.yaml` declares three variants — `sky130_fd_sc_hdll`,
`sky130_mix9t` (union) and `sky130_ll9t` (leakage-first) — each with its own
corners, `native_dff` and `cts:` limits.  `scripts/gen_ip.py` merges a
variant's `cts:` block over the family default, so a slower family is not
measured against the default family's transition/capacitance ceilings.

## 4. Measured PPA (`spec/bang_bang_4x.yaml`, 25 ns, LibreLane Classic)

All runs: same RTL, same harness (`scripts/run_cross_pdk_pnr.py`), same
25 ns period, Magic/KLayout DRC and Netgen LVS enabled.  Today's netlist is not
the one behind the older recorded sky130 closure (140 DFFs here vs 126 there),
so the `hd` row below is a freshly re-measured baseline, not the stored 25 ns
record.  At 25 ns neither `hd` nor the mix closes SS; the useful comparison is
the delta between them.

| library | status | cells (std/DFF) | core µm² | power µW | netlist leakage | SS WNS | TT WNS | DRC (route/magic/klayout) | LVS |
|---|---|---|---|---|---|---|---|---|---|
| `sky130_fd_sc_hd` (baseline) | measured_with_violations | 2131 (1336/140) | 15089.5 | 1.957 | 6.29 nW | **-2.300** | 0 | 0/0/0 | 0 |
| `sky130_fd_sc_hdll` | measured_with_violations | 3171 (2707/140) | 28469.8 | 1.742 | 163.3 nW* | -3.647 | 0 | 0/0/0 | 0 |
| `sky130_mix9t` (hd ∪ hdll) | measured_with_violations | 2125 (1320/140) | 15089.5 | **1.191** | 9.45 nW | **-1.859** | +3.797 | 0/0/0 | 0 |
| `sky130_ll9t` (hdll + 145 hd extras) | measured_with_violations | 2760 (2327/140) | 26058.7 | 1.723 | 190.3 nW* | -5.521 | 0 | 0/0/0 | **328 (open)** |

\* dominated by the upstream `dfrtp_4` leakage error (see above); excluding
that cell the hdll-family leakage is ~20 nW.  Leakage is summed from the final
netlist and the Liberty `cell_leakage_power` rather than taken from the flow's
`power_leakage_mw`, which reports 12 pW for the `hd` run (three orders of
magnitude below the netlist sum) and is not usable for a leakage comparison.

Reading of the numbers:

- **The plain union (`mix9t`) is the best result of the four.**  Same area as
  the baseline (15089.5 µm²), 39% lower total power (1.191 vs 1.957 µW), 0.44 ns
  better SS WNS and +3.8 ns at TT, DRC and LVS clean.  The mapping differs from
  the baseline (1320 vs 1336 std cells, different ABC solution), so this is a
  *library* effect, not a like-for-like cell swap; it is reproducible from
  `build/pnr/close25_mix9t.json`.
- **`hdll` alone is the wrong trade at 25 ns**: +89% area, -1.35 ns WNS, and
  leakage dominated by one mis-characterised cell.
- **`ll9t` (leakage-first) is the worst of the four**: hdll cells are slower and
  weaker at the same drive, so the resizer spends 2327 std cells to reach
  -5.52 ns.  A low-leakage family only pays at a relaxed period.
- All four are `measured_with_violations` at 25 ns because SS setup does not
  close for this netlist; the comparison is the delta, not the absolute sign.

### The leakage-first variant (`sky130_ll9t`)

The plain merge above is a *union*: hd and hdll tie on footprint and drive, so
ABC has no reason to prefer the low-leakage cell.  The ics55 pattern is the
opposite — one family is the default and the others only fill gaps.  So the
second variant keeps **all 324 hdll cells** and adds only the **145 hd cells
hdll does not have** (ICG `dlclkp`, `dfrtn`, tristate `ebufn`/`einvn`/`einvp`,
`lpflow_*`, delay cells, `probe`, and the `_0` drive variants).  Every function
that exists in both families is then unambiguous and resolves to hdll.

It also exposed a real flow bug: the design-rule limits were the hd ones
(`set_max_transition 0.5`, `set_max_capacitance 0.1 pF`).  hdll characterises
**no pin below 1.0 ns** `max_transition`, so 0.5 ns is unsatisfiable by
construction and `repair_design` inserts buffers without bound — the first
hdll run sat in `repair_designpostgpl` for over an hour.  `tech.yaml` now
carries a per-variant `cts:` block (1.0 ns / 200 fF for hdll and ll9t), which
`scripts/gen_ip.py` merges over the family default.  This is the same
mechanism the ics55 flow needs for H7CH/H7CL/H7CR.

### Why the plain merge did not pay off

The mixed run mapped **1355 hd cells and 3 hdll cells**: at equal
footprint/drive the two families tie on area, so ABC has no reason to pick the
low-leakage cell, and its tie-breaking landed on `hd`.  The merge is therefore
not "hd + a few low-leakage cells" but "a different library", and the extra
library members perturb ABC's solution (DFF 140 vs 126 in the baseline).  Post-
route, SS has 117 max-slew violations and 17 setup violations, and the worst
path is entirely `hd` cells (`maj3_4`, `nand2_4`, ...), i.e. the mapping/repair
spiral — not hdll insertion — is what costs the 1.86 ns.

Conclusion: a mixed library only helps with an explicit selection policy.
Options, in order of effort:

1. **All-hdll at a relaxed period** (see the `hdll` row): the low-leakage
   family is a single-library answer with no selection problem.
2. **Leakage ECO**: route with `hd`, then swap instances with positive slack
   to the `hdll` cell of the same drive (`set_dont_use hdll*` during
   implementation, then a slack-gated ECO).  The 752-cell merged view already
   contains both name sets, so the swap is a rename plus a legalisation pass.
3. **Set-level policy** (`set_dont_use hd*` on non-critical logic groups) is
   not expressible in the current LibreLane config without a custom step.

## 5. Limits

- `sky130_fd_sc_hdll` has no clock gate (`dlclkp`); the mix keeps `hd`'s
  `dlclkp_1`, so clock gating stays available only through the hd part.
- `as_sc_hs` mixes only if its own LVS story is accepted (no golden netlist);
  it has no async-reset flop in the core set, which blocks the current RTL.
- Mixed rows require identical heights; `ms`/`ls`/`lp` (3.33 µm) can be mixed
  among themselves but not with the 2.72 µm group.
- The installed `ms` tree has no per-cell TT Liberty, so an all-`ms` PPA run
  would be SS-only.
