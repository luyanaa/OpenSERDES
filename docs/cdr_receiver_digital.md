# CDR and receiver: what is actually digital?

This note separates the digital content of the original CDR and receiver from
the analog/back-end content, based on the delivered files:

- `OverSampling_CDR/README.pdf` and the OpenSerDes paper (arXiv:2105.13256).
- `OverSampling_CDR/CLK_RECOVERY.lvs.v` — the delivered gate-level LVS netlist.
- `Receiver_Bypassing_CDR/Receiver_README.pdf` and `Receiver.sp`.
- `rtl/cdr_oversampling.sv` and `rtl/serdes_rx_digital.sv` for the new model.

There is no original RTL for the CDR or receiver in the repository.  The CDR
analysis below is a structural inference from the LVS netlist, not a recovered
source file.

## 1. CDR: a digital period tracker, not a phase interpolator

### 1.1 Partition

The delivered CDR is entirely standard-cell/digital.  It has:

- a reference-clock domain (`CLK_IN`);
- a digital divider/clock generator that produces `CLK_OUT`;
- edge/run-length measurement counters;
- a decision/correction block driven by `SCAN_IN[21:0]`;
- a final data retiming flop clocked by the generated `CLK_OUT`.

The PDF block diagram calls the pieces “multiphase clock generator”, “multiple
samplers”, “FIFO registers”, “bit boundary detection”, “decision block”,
“glitch correction scan” and “jitter correction scan”.  The delivered LVS
netlist is much smaller than that diagram suggests; see §1.4.

### 1.2 State inventory from `CLK_RECOVERY.lvs.v`

The netlist has 9542 instances in total (mostly fill/decap/diode/tap), 1021 non-fill logic cells, and 75 flops.  The named state is:

| State | Width | Inferred role |
|---|---:|---|
| `DIVISOR_COUNT` | 11 | Counts `CLK_IN` cycles to generate `CLK_OUT`. |
| `COUNT_MIN` | 10 | Minimum measured edge/run interval; sets the bit-period estimate. |
| `COUNT_BB_ZERO` | 11 | Consecutive-zero run length in reference-clock cycles. |
| `COUNT_BB_ZERO_MIN` | 11 | Captured/minimum zero run length. |
| `COUNT_BB_ONE` | 11 | Consecutive-one run length. |
| `COUNT_BB_ONE_MIN` | 11 | Captured/minimum one run length. |
| `MULTIBIT_ZERO`, `MULTIBIT_ONE` | 1 each | Run is longer than a single bit; hold the previous period. |
| `ONE_CHECKED`, `ZERO_CHECKED` | 1 each | Validity/qualification of the measured one/zero runs. |
| `P_CLK`, `N_CLK` | 1 each | Edge-history/sample flops for `BB_IN`. |
| `RESET_LIFTED_N`, `RESET_LIFTED_P` | 1 each | Reset-release sequencing. |
| `CLK_OUT` | 1 | Generated recovered clock. |
| `DATA_OUT` | 1 | `BB_IN` retimed by `CLK_OUT`. |

`CLK_OUT` is a flop output on the reference clock.  `DATA_OUT` is a flop whose
clock is `CLK_OUT`, so the recovered clock is a real generated clock, not just
a monitor.  `SCAN_IN[21:0]` is not a scan chain: it is a static 22-bit
configuration bus feeding the decision/correction logic; there is no
`SCAN_OUT` and no 22-bit scan register.

### 1.3 Inferred algorithm

The structure is consistent with this digital frequency-locked loop:

1. `P_CLK`/`N_CLK` capture the incoming serial level and its edge history.
2. `COUNT_BB_ONE` / `COUNT_BB_ZERO` count the duration of one and zero runs in
   `CLK_IN` cycles.
3. `COUNT_BB_ONE_MIN` / `COUNT_BB_ZERO_MIN` retain the shorter observed run;
   `COUNT_MIN` is the global minimum used as the bit-period estimate.
4. `DIVISOR_COUNT` counts `CLK_IN`; when it reaches the selected period,
   `CLK_OUT` toggles.  This is a digitally divided recovered clock.
5. `MULTIBIT_ONE` / `MULTIBIT_ZERO` keep the last valid period when the data
   stays at one level for more than one bit (no edge to measure).
6. `ONE_CHECKED` / `ZERO_CHECKED` qualify the measurement.
7. `SCAN_IN[21:0]` trims the glitch/jitter decision thresholds.  The PDF labels
   the two groups as glitch and jitter correction; the exact bit split is an
   inference from the two 11-bit run counters.
8. `DATA_OUT` is `BB_IN` retimed by the recovered `CLK_OUT`.

This is **frequency/period tracking with edge-based phase alignment**, not a
PLL and not a phase interpolator.  It measures the bit period from data
transitions and divides the reference clock accordingly.

### 1.4 Paper vs delivered netlist

The paper says the CDR “samples the received data at multiple points, stores
them in FIFO registers” and has a multiphase clock generator.  The delivered
LVS netlist does **not** contain a FIFO register bank, a multiphase sampler
array, or a 22-bit scan register.  Its state is the counter/divider/retimer
set listed above.

So the delivered `CLK_RECOVERY` is a compact digital period-tracking CDR.  The
PDF block diagram should be treated as architectural intent; it is not a
one-to-one description of the delivered netlist.  This is important for
migration: reproducing the PDF diagram would create a different block from the
one that was actually taped out.

### 1.5 Digital meaning and limits

- Digital function: measure data run lengths, choose a clock divisor, generate
  `CLK_OUT`, and retime `DATA_OUT`.
- It is area-efficient and synthesizable, but the period estimate has
  reference-clock resolution and no continuous phase tracking.
- Long runs of identical bits rely on the held period; frequency drift during
  a long run is not tracked.
- Glitch/jitter correction is a static trim, not an adaptive loop.
- The recovered clock is a divided reference; its jitter is dominated by the
  reference clock and divider edges.
- The delivered README result is a **50 kHz** CDR clock, and the “Maximum
  Frequency of Operation” field is blank.  The paper reports a 2 Gbps link, but
  there is no delivered max-frequency sweep for the CDR.  Do not present the
  50 kHz demo as a 2 GHz CDR characterization.

## 2. Receiver: analog slicer + one digital sampling latch

### 2.1 Partition

The receiver is **not** a digital deserializer.  It is a 1-bit analog front-end
followed by a clocked sampling element:

```text
INP -> resistive-feedback inverter -> CMOS inverter -> NAND-based DFF -> OUT / OUT_B
                                      analog          digital/retiming
```

The analog part:

- resistive-feedback inverter (self-biased near `VDD/2`) senses the small
  input and provides gain;
- CMOS inverter restores rail-to-rail level.

The digital/retiming part:

- a custom NAND-based master–slave flip-flop, clocked by `CLK`;
- outputs `OUT` and `OUT_B`.

The receiver documentation says “Receiver with CDR bypassed”, 2 GHz clock,
500 mVpp input, 1.8 Vpp output.  There is no CDR and no deserializer inside
this block.

### 2.2 Digital content in `Receiver.sp`

The top `Receiver` subcircuit uses:

- `X12`–`X19`: eight NAND gates forming the clocked latch/flip-flop;
- `X25`, `X26`, `X31`: inverter stages for data/clock;
- `M0`–`M8` and the PMOS feedback devices: the analog inverter/feedback stage.

The NAND flip-flop is a custom transistor-level cell, not a standard-cell
`DFF`.  Its digital meaning is simply “sample the slicer output on the clock
edge and hold a complementary output.”  It is the boundary between the analog
receiver and the digital CDR/deserializer.

### 2.3 Digital meaning and limits

- The digital part is a 2 GHz retiming latch; it does not recover a clock.
- The sampling clock must be supplied externally; in the full link this can be
  the CDR output or a forwarded clock.
- At 2 GHz, the NAND latch's setup/hold, clock feedthrough and output swing are
  analog-sensitive; replacing it with a generic synthesized DFF changes the
  receiver.
- The receiver has no serial-to-parallel function; deserialization starts after
  this block.

## 3. Comparison with the new RTL

| Aspect | Original CDR | New `cdr_oversampling.sv` |
|---|---|---|
| Clock source | Reference `CLK_IN`; output divided by measured period | External oversampling clock, assumed `OVERSAMPLE × data rate` |
| Frequency acquisition | Measures data run lengths; sets `DIVISOR_COUNT` | None; fixed oversampling ratio |
| Phase alignment | Edge/run-based, period estimate | Edge loads phase counter; free-runs after lock |
| Recovered clock | Real `CLK_OUT` (used to clock `DATA_OUT`) | `clk_out` monitor; data uses `sample_strobe` |
| Scan/trim | 22 static correction bits | None |
| FIFO/multiphase | Not present in delivered netlist | Not present |
| State | 75 flops, counters/divider | Small phase/lock counter |

| Aspect | Original receiver | New RX digital |
|---|---|---|
| Analog front-end | Resistive-feedback inverter + CMOS inverter | Not modeled; RX starts at digital serial input |
| Sampler | Custom 2 GHz NAND DFF | Standard-cell/behavioral flop in CDR/deserializer |
| CDR | Bypassed | fixed/adaptive/source-sync CDR in `serdes_rx_digital` |
| Function | 1-bit retiming | CDR + serial-to-parallel + comma frame alignment |

The new CDR is therefore a **different architecture**: fixed-rate oversampling
or a first-order frequency-tracking loop, while the original is a digital
period-tracking frequency-locked CDR.  It is suitable as a clean portable
model, but it does not preserve the original CDR's scan trim or recovered
clock semantics.

## 4. Migration implications

1. **Implemented:** `serdes_rx_digital` has `CDR_MODE` = fixed (0), adaptive
   (1) or source-sync (2); `CDR_BYPASS=1` remains an alias for mode 2.  In
   source-sync mode the external `rx_clk` is the bit clock, `receiver_sampler`
   retimes the serial input, and the oversampling loop is skipped.  The
   generator selects the mode from `rx.clocking` and `rx.ppm`.
2. **Implemented:** `rtl/receiver_sampler.sv` models only the receiver's
   digital `q <= d` sampling boundary.  The 2 GHz NAND DFF and the analog
   front-end remain hard-macro / legacy content; do not synthesize them as a
   generic DFF and claim receiver equivalence.
3. **CDR contract:** implemented as two explicit modes:
   - `fixed_oversampling`: frequency acquisition is external; use when TX/RX
     clocks are locked.
   - `adaptive_oversampling`: first-order frequency tracking; use for bounded
     ppm (validated at +/-700 ppm and 100 ppm for 5000-bit runs with
     OVERSAMPLE=8, FRAC_BITS=12).  Jitter tolerance, gate-level equivalence and
     the analog sampler remain open; a production high-jitter link still needs
     a second-order loop or phase interpolator.
   The original run-length/min-period/divider/scan-trim algorithm is still not
   cloned; that would be a separate mode if gate-level equivalence is needed.
   Frame alignment is implemented separately in `rtl/comma_align.sv`.
4. **Extract behavioral checks from the netlist**:
   - sweep `SCAN_IN[21:0]` and measure `CLK_OUT` period/lock;
   - drive long runs of ones/zeros to exercise `MULTIBIT_*` and held-period
     behavior;
   - compare `DATA_OUT` against `BB_IN` at the recovered clock;
   - receiver transient at 2 GHz with 500 mVpp input.
5. **Keep the analog boundary explicit** in the top-level diagrams:
   `Receiver.sp` ends at `OUT/OUT_B`; `serdes_rx_digital` begins at
   `rx_serial_in`.

## 5. Bottom line

- CDR digital meaning: a **digital period-tracking clock divider and data
  retimer**, with static glitch/jitter trim.  The delivered netlist is not the
  FIFO/multiphase block drawn in the PDF.
- Receiver digital meaning: a **2 GHz one-bit sampling/retiming latch** after an
  analog slicer.  It is not a CDR and not a deserializer.
- The new RTL migrates the **digital data path, fixed/adaptive CDR and comma
  frame alignment**, not the original CDR's scan-trim algorithm or the
  receiver's analog sampler.  Those remain explicit migration items.

## 6. Lower-oversampling (2x / baud-rate) CDR: measured negative result

Item 6 of the PPA audit asked for a 2x baud-rate bang-bang CDR to cut the RX
clock from OVERSAMPLE x line rate to 2x.  Investigation on 2026-11-10 shows
this is **not reachable by parameterising the existing loop**:

| Test | Result |
|---|---|
| `cdr_adaptive` OS=8, 0 / +700 / -700 ppm, 5000 bits | PASS (freq_trim 0 / +47 / -53) |
| `cdr_adaptive` OS=4, 0 ppm, 5000 bits | FAIL, ~2405 bit errors |
| `cdr_adaptive` OS=4, SAMPLE_BASE offset 0..3 | FAIL, ~2405 errors each |
| `cdr_adaptive` OS=4, N_CHECK=1500 (shorter) | FAIL, ~650 errors |

Structural cause, not a tuning problem:

- `PHASE_OS_S = OVERSAMPLE + 2` while `phase_q` wraps at `OVERSAMPLE - 1`.
  For OVERSAMPLE < 8 the resulting `phase_err` has no negative branch
  (OS=4: phase_err is {0,1,2,-3}, and |−3| exceeds `PHASE_HALF_S`), so the
  loop can only push one way.
- For OVERSAMPLE = 2, `SAMPLE_BASE = HALF + 1 = 2` lies outside the period
  `0..1`, so the data sample is never selected.

A genuine 2x CDR therefore needs a different signal path, not a lower
`OVERSAMPLE`:

1. **Phase interpolator / multiphase clock** (analog or a delay-locked ring),
   so the sampling instant can move sub-UI; or
2. **DDR / both-edge sampling** with a quadrature clock; or
3. **A 3-way phase-error detector** that derives a signed error from two
   samples plus the previous UI's boundary (a new `cdr_bang_bang` module, not
   a parameter of the current counter).

Until one of those exists, the low-power path is **source_sync (OS=1)** or the
restricted **fixed OS=2** mode (`rx.allow_os2: true`), both already supported.
The generator's `bang_bang` / `pi_cdr` options therefore stay fail-closed.
Do not lower `adaptive_oversampling.min_oversample` below 8 without a new
detector and a passing `tb_cdr_adaptive` at OS=4.

The testbench is now parameterised (`-P tb_cdr_adaptive.OS_PARAM=<n>`) so the
OS=4 failure can be reproduced.

## 7. 4x bang-bang CDR: BBPD + 2nd-order loop + fractional NCO (item 6)

`rtl/cdr_bang_bang.sv` is a purpose-built lower-oversampling CDR.  It runs at
4x the line rate (half the clock load of the 8x adaptive loop) and is wired
into `serdes_rx_digital` as `CDR_MODE=3`.

Architecture:
- **Alexander BBPD** on (previous data, edge, data) feeds a signed vote
  v[n] in {-1, 0, +1}.
- **Digital loop filter**: frequency integral `f += KI*v`, proportional phase
  kick `phi -= KP*v` (both with the correct sign: an "early" vote delays the
  sampling instant), plus a slow leak on `f` that removes the PD bias at
  zero ppm.
- **Fractional NCO**: a 20-bit fractional phase accumulator advances by
  `1 + f` clock periods per clock and wraps once per UI.  A UI is 4 clocks
  nominally and 3/5 when the NCO is off-nominal, so the recovered bit rate
  follows the TX continuously.
- **Sampling**: the data sample is captured when the NCO crosses the eye
  centre (shifted by `N mod OVERSAMPLE` to compensate the cdr_sync delay);
  the edge sample when it crosses the delayed UI boundary.  Exactly one
  `sample_strobe` is emitted per UI, so a 3-cycle UI cannot skip the centre
  and a 5-cycle UI cannot duplicate a bit.

Verified by `tb/tb_cdr_bang_bang.sv` (5000 bits, OS=4):

| ppm | result |
|---:|---|
| 0 | PASS |
| +100 | PASS |
| +300 | PASS |
| +700 | PASS |
| -100 | PASS |
| -300 | PASS |
| -700 | PASS |

The generator exposes this as `rx.architecture: bang_bang_oversampling`
(`CDR_MODE=3`, `OVERSAMPLE=4`); it must be selected explicitly so it does not
silently replace the default fixed loop.  `spec/bang_bang_4x.yaml` is the
reference; it is in `make sim` / `regress` and the generated package is
compile/synth checked.

Limits (do not overclaim): the loop is a digital BBPD + NCO, not a jitter-
filtering PLL; 4x sampling has less jitter margin than 8x; `freq_trim` carries
the loop state, not a calibrated ppm; and the sky130 mixed-signal baseline in
this repo uses an open-loop phase search, so none of this constitutes a
physical link-level CDR signoff.
