# Clock-tree / skew audit (corrected)

## 1. Actual clock-tree structure

### Deserializer

The Deserializer already has a CTS tree.  The effective structure is:

```text
Level 0:  clkbuf_16 (_CTS_root)                 1 root buffer
              │
Level 1:  clkbuf_4 × 19 (_CTS_buf_1_*)         19 mid buffers
          ╱          │          ╲
Level 2: clkbuf_4 × 2 (_CTS_buf_2_*)            2 leaf buffers
          ╱                    ╲
       DFF group             DFF group
       (~27 per branch)      (~27 per branch)
```

- 21 × `clkbuf_4` + 1 × `clkbuf_16`
- 556 DFF
- The tree is **3 levels**, not 7.  An earlier buffer-chain depth count treated every local leaf buffer as another CTS level; that was the wrong metric.

The problem is **fanout balance**, not depth:

- The 19 mid `clkbuf_4` stages drive roughly 28 DFF each, which is too high.
- Only 2 stages go to a Level-2 buffer; the rest drive DFF directly.
- A better shape is root → 8 mid buffers → leaf buffers → 8–10 DFF per leaf, still 3 levels, with skew < 30 ps.

Separate netlist observation: the text of
`DeSerializer/deserialiser_unit_cell_1.lvs.v` contains 300 `dfrtp` instances
without an explicit `.CLK` connection, and 256 with `.CLK(COMPLETE)`.  That is a
netlist export/connection defect, not evidence that the logical design lacks
CTS.  It must be fixed before using that netlist as a timing reference.

### Serializer

The original Serializer is genuinely a 2-level tree:

```text
Level 0:  clkbuf_16                1 root
          ╱          ╲
Level 1: clkbuf_1    clkbuf_1      2 leaves
          ╱              ╲
       DFF × 6-7      DFF × 6-7
```

- 13 DFF
- Leaf fanout 6–7
- Estimated skew ≈ 15 ps
- **2 levels are sufficient for the original 13-DFF block.**

Important: the abstract serializer restores the 256-bit input register bank
that the original synthesized netlist deleted.  It therefore has ~266 DFF, not
13.  The original 2-level tree is not a valid template for the abstract
serializer; the abstract version needs a 3-level, fanout-8–10 tree.

### CDR

- 47 core DFF in the original netlist (the original also contains 28 `dfstp`
  instances on generated set/reset/clock nets; those generated clocks should be
  removed).
- 2–3 levels are sufficient for the original block.
- Estimated skew ≈ 20–40 ps with fanout 8–16.
- Use `sample_strobe` as a data enable, not a clock.

New RTL structure:

- `cdr_sync` adds `SYNC_STAGES` flops (default 3).  Both edge detection and data
  selection use the oldest stage, so the data path really passes through the
  whole synchronizer.  Do not retime/merge these flops in the physical flow;
  the RTL cannot guarantee MTBF.
- `cdr_oversampling` is fixed-phase; `cdr_adaptive` adds a fractional period
  loop (`FRAC_BITS`, `LOOP_GAIN`, `LOOP_SETTLE_BITS`).  Adaptive uses the same
  clock but has extra counters/registers; budget a separate, slightly higher
  area/power for mode 1.
- OVERSAMPLE=2 is a restricted mode (two samples/UI, no jitter margin).  Use
  it only when the generator spec explicitly sets `allow_os2: true`.
- `rx_frame_locked` and `rx_lock_stale` are status outputs, not clocks.

## 2. Depth is not “longer is better”

Adding levels:

- reduces per-level fanout → can reduce skew and improve edge rate;
- increases insertion delay (~50–100 ps per buffer);
- increases dynamic power \(P = \alpha C V^2 f\);
- increases area.

The design target is **balanced fanout and controlled skew**, not maximum depth.

## 3. Quantitative comparison

| Design | DFF | Current levels | Current fanout | Estimated skew | Recommendation |
|---|---:|---:|---:|---:|---|
| Serializer (original) | 13 | 2 | ~7 | ~15 ps | 2 levels sufficient |
| Serializer (abstract, 256-bit input regs) | 266 | needs CTS | target 8–10 | target < 40 ps | 3 levels, balanced |
| Deserializer | 556 | 3 | ~8–28 | ~40–80 ps | 3 levels, balance to 8–10 |
| CDR | 47 core | 2–3 | ~8–16 | ~20–40 ps | 3 levels |
| `serdes_digital_top` | 550 | needs CTS | target 8–10 | target < 50 ps | 3 levels |

## 4. Cross-process guidance

| Process | Max fanout/level | Serializer (13 DFF) | Deserializer (556 DFF) |
|---|---:|---|---|
| Sky130 (130 nm) | 8–12 | 2 levels | 3 levels |
| GF180 (180 nm) | 8–12 | 2 levels | 3 levels |
| IHP SG13G2 (130 nm) | 8–12 | 2 levels | 3 levels |
| 55 nm | 10–16 | 2 levels | 3 levels |
| 0.5 µm | 4–6 | 2–3 levels | 4 levels |

Larger geometries have higher wire resistance, so they need lower fanout and
more levels.  This should be handled by CTS constraints, not by hand-inserting
buffers in RTL.

## 5. CTS constraints

OpenLane-style starting point:

```tcl
set ::env(CLOCK_TREE_TARGET_SKEW) 50    ;# ps
set ::env(CTS_MAX_SLEW) 0.5             ;# ns
set ::env(CTS_MAX_CAP) 100              ;# fF
set ::env(CTS_MAX_FANOUT) 10
```

LibreLane exposes `CTS_MAX_CAP` and `CTS_MAX_SLEW` directly.  `CLOCK_TREE_TARGET_SKEW`
and `CTS_MAX_FANOUT` are OpenROAD/CTS-tool-specific; keep them in the tech
profile and pass them to the CTS step supported by the backend.  Do not assume
they are silently honored.

The SDC template uses 50–100 ps setup uncertainty.  After CTS and extraction,
re-derive uncertainty from the actual skew and jitter; do not keep the pre-CTS
value.

## 6. Rules for the abstract RTL / generator

- No derived clocks. `sample_strobe`, `par_valid`, `frame_done`, `rx_frame_locked` are enables/pulses/status, not clocks.
- One clock per domain; reset through `reset_sync`.  `flush` is synchronous state reset, not a clock.
- Treat the CDR synchronizer flops as a fixed-latency asynchronous boundary; do not let CTS/retiming merge them with the phase counter.
- Balance leaf fanout to 8–10 for 130/180 nm, 10–16 for 55 nm, 4–6 for 0.5 µm.
- For 556 DFF use 3 levels; for 0.5 µm use 4.  Do not simply add depth.
- The original Serializer 2-level tree is fine for 13 DFF; it is not fine for the abstract 266-DFF serializer.
- `rx_clk_out` is a monitor output; do not time it as a functional clock.
- Pre-CTS reset recovery/removal paths are excluded in the SDC template until a
  reset tree is inserted; re-enable them for signoff.

## 7. Reproduce

```bash
python3 scripts/analyze_clock_tree.py \
  /grid/OpenSERDES/Serializer/serializer_unit_cell_1.lvs.v \
  /grid/OpenSERDES/DeSerializer/deserialiser_unit_cell_1.lvs.v \
  /grid/OpenSERDES/OverSampling_CDR/CLK_RECOVERY.lvs.v
```

The script reports buffer-chain connectivity and fanout.  It is **not** a CTS
level counter: local leaf buffers can make the chain look deeper than the
intended CTS hierarchy.  Use the tree shape and fanout, not the raw depth, for
the skew conclusion.
