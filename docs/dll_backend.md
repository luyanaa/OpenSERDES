# DLL phase-select backend for the 4x bang-bang CDR

Status: digital half implemented and verified (`rtl/cdr_multiphase.sv`,
`tb/tb_cdr_multiphase.sv`), and wired into the RX endpoint as `CDR_MODE=4`
with an external sampler interface (`rx_dll_tap`/`rx_smp`).  With the
boundary-add pipeline it closes 10 ns pre-PnR by 1.9 ns; routed it is
**~10.08 ns (~99.2 MHz) with the flow's 0.25 ns uncertainty**, i.e. 0.08 ns
short of 100 MHz, and +0.31 ns better than the non-pipelined core in a
matched A/B.  The delay line has first-order and closed-loop SPICE checks
(§5.1, §5.2) with a behavioural PD/CP; the phase mux, sampler and transistor
PD/CP are not built, and there is no fabricated or PEX-characterised DLL.

## 1. Why the current loop stops at 12-13 ns

`cdr_bang_bang` (with C2, commit a587565) resolves one 100 MHz clock period to
2^-16 in a 16-bit fractional NCO and derives the sampling instant from the
accumulator carry.  At the 10 ns target the routed result is 12.04 ns / WNS
-1.32, i.e. 77-83 MHz.  The remaining depth is arithmetic width: the
accumulator plus threshold compare has to settle inside a 10 ns cycle because
the sample edge depends on it.

The NCO width is not actually needed.  The phase detector only has to place a
sample on a 4x grid; a 1/32-UI step (1.25 ns) is finer than the loop's own
quantisation.  A DLL can supply those 32 positions as 8 phases of the 100 MHz
clock, so the digital loop only has to *select* a phase and never has to
generate the edge.

## 2. Architecture

```
          100 MHz ref
               |
          +---------+   8 phases, 1.25 ns spacing
          |   DLL   |--------------------------------+
          +---------+                                |
               |                                     |
               |  tap[2:0]                           |
               v                                     v
        +--------------+   selected phase    +---------------+
        |  phase mux   |-------------------->|   sampler     |
        +--------------+                     +-------+-------+
                                                     | smp (one clk later)
                                                     v
                                            +------------------+
                                            | cdr_multiphase   |
                                            |  slot/fine loop  |
                                            |  BBPD + filter   |
                                            +--------+---------+
                                                     |
                                                     +--> tap[2:0]
```

The digital core runs on the reference clock.  There is no analog timing path
inside it, and the sampler hand-off is a normal register: the sample is taken
on the selected phase and read on the next reference edge.

### 2.1 Phase representation

```
slot_q[1:0]   which of the 4 samples in a UI this cycle is
tap_q[2:0]    which of the 8 DLL phases the sampler uses
frac_q[7:0]   sub-tap frequency resolution (122 ppm per LSB at OS=4)
```

One clock advances `slot_q` by one.  Only at the last slot does the fine word
change:

```
fine = {tap, frac}
fine += freq - kick          // once per UI, at the last slot
```

so the tap is constant for all four samples of a UI.  The tap is a register
slice - nothing between the loop state and the phase mux - and the loop's only
adder is the 13-bit `{tap, frac}` add, which is clock-enabled once per UI.

A tap wrap is a 10 ns slip of the sampling phase, so it also moves which sample
of the UI is the centre; the carry advances the slot field on top of the
clock's own +1.  Losing that carry makes the loop lock at 0 ppm and fall apart
as soon as the tap starts wrapping (measured: 0 ppm passed, +-700 ppm failed
with 35% BER).

### 2.2 Event detection

Centre and edge events are threshold crossings on the 5-bit `{slot, tap}` phase,
the same construction as `cdr_bang_bang`, not slot-equality compares.  A slot
compare can only capture if the accumulator starts on the data eye; a crossing
restores the full one-UI capture range.  The event pulse is registered once so
it lands in the cycle where the core reads the sample it classifies.

The centre sits at `phase_offset` slots and the edge half a UI later, so the
existing phase-offset tests apply unchanged.

## 3. Timing

sky130 `ss_100C_1v60`, yosys 0.52 + `abc -liberty`, `sta` on the mapped
netlist, no PnR:

| block | critical path | WNS at 10 ns | cells |
|---|---:|---:|---:|
| C2 `cdr_bang_bang` (pre-PnR) | 13.85 ns | -5.76 | 2109 (10 ns run) |
| `cdr_multiphase` core, as merged | 9.10 ns | +0.90 | 512 |
| **`cdr_multiphase`, pipelined boundary add** | **7.77 ns** | **+1.93** | **544** |

The pipelined row registers the UI step (`step_ui_q`) one UI ahead, so the
phase adder's second operand is a register.  Before that, the path was
`freq_q -> freq-kick -> phase add -> threshold compare -> centre_hit` at
8.79 ns.  The cost is one extra UI of correction latency (two instead of
one) and 13 more flops; the core is 544 cells.  The second-order loop does
not care: 40 ns is far below its time constant, and the 100k-bit runs below
still lock with 0 errors.  As before, the cell count is the core only (no
deserialiser, `cdr_sync` or sampler), so it is not a drop-in replacement for
the C2 number; it is the loop arithmetic that changed.

Routed (LibreLane 3.0.14, sky130 ss_100C_1v60, 10 ns target, 0.25 ns clock
uncertainty, 2 ns input delay except 0.3 ns on `smp` and `rst_n`): the
historical 3.0.17 run without the pipeline was **WNS -0.46 ns** (~10.5 ns).
A matched A/B re-run in 3.0.14 with the same SDC gives **-0.143 ns** for the
non-pipelined core and **+0.167 ns** for the pipelined one (that SDC omits
the uncertainty); with the uncertainty the pipelined core is **-0.083 ns**,
i.e. ~10.08 ns (~99.2 MHz) with one violating path.  So the boundary-add
pipeline is worth **+0.31 ns routed** and takes the core from ~10.4 ns to
within 0.1 ns of the target.  The remaining path is the phase add ->
threshold compare (`ph_q` -> `ph_next_ui` -> `centre_hit`/`edge_hit`), not
the frequency correction.

`FREQ_FRAC=7` was tried as a way to lower the pre-PnR adder depth (8.84 ns vs
9.10).  Routed it came out at -0.66 ns, worse than the -0.46 above, so the
placement/CTS noise dominates the last adder bit and the committed value stays
`FREQ_FRAC=8` (122 ppm per LSB).  This is the same lesson as C3: do not choose
an RTL variant from pre-PnR depth alone.  The boundary-add pipeline is the
variant that did survive: +1.03 ns pre-PnR and +0.31 ns routed in the matched
A/B.

## 4. Functional verification

`tb/tb_cdr_multiphase.sv` models the phase mux and sampler at
`(tap + 0.5) * 1.25 ns`, i.e. taps in the middle of each 1.25 ns slot, and
reads the result on the next clk.

| check | result |
|---|---|
| 0 / +-700 / +-1000 ppm, 5000 bits | 0 errors |
| patterns 0-3 (PRBS, 256-bit run, bursty, 6% density) at +-700 ppm | 0 errors |
| phase_offset 0-3 at 0 and 700 ppm | 0 errors |
| frequency steps 0->700, 700->-700, -1000->+1000 | 0 errors, relock 0 bits |
| capture, 5000 bits | -4000..+4000 ppm pass; +5000 fails |
| 100k bits at 0 / -1000 / +1000 ppm | 0 errors; freq span 6 / 32 / 32 LSB |
| lint (`verilator -Wall`) | clean |

Residual: at +3000 ppm the 5000-bit run shows 10 errors during acquisition.
The -4000/+4000 points pass, so this is a pull-in transient in the capture
sweep, not a hard capture edge; it was not re-run with a longer window.
The capture sweep is unchanged by the boundary-add pipeline (+3000 still
shows the same 10 acquisition errors), and the frequency dither grows by
~2 LSB (6/32/32 vs 4/31/30) because the correction now lands one UI later.

`tb/tb_rx_endpoint_dll.sv` repeats the 0/+-700 ppm checks through the full
`serdes_rx_digital` endpoint (`CDR_MODE=4`) with the same `(tap+0.5)*1.25 ns`
sampler model, plus pattern 3 at +700 ppm.  All pass, and the endpoint probe
confirms the delivered loop parameters (`PH_W=3`, `FREQ_FRAC=8`, `KI=1`,
`KP_SHIFT=2`, `LEAK_UI_BITS=12`) rather than a hand-written set.  The
generator does not emit a mode-4 package yet (section 6).

## 5. Analog DLL specification

The digital half assumes this macro.  Values are targets, not measurements.

| parameter | target | note |
|---|---|---|
| reference | 100 MHz, 1.0-1.8 V | same clock the digital core uses |
| phases | 8, 1.25 ns nominal spacing | monotonic, within one period |
| lock range | +-10% reference period | covers +-4000 ppm with margin |
| static phase error | < 30 ps | tap mismatch directly adds to CDR jitter |
| period jitter | < 2 ps RMS (1 kHz-100 MHz) | must stay below 0.5% UI |
| duty cycle | 45-55% | sampler is single-edge |
| power | < 2 mW | 8-phase VCDL + PD + charge pump + bias |
| area | < 0.02 mm^2 | 10 ns delay line ~ 150-200 inverter stages |

Suggested implementation: 8-tap voltage-controlled delay line (current-starved
inverters or RC-loaded stages) locked to one reference period by a bang-bang
phase detector and charge pump.  Because the total delay is one 10 ns period
and each tap is 1.25 ns, tap mismatch is the dominant error term; a
dummy/replica biasing scheme and symmetric tap loading are required.

### 5.1 First-order delay-line feasibility (measured)

`verification/analog/dll_feasibility.py` builds an 8-tap current-starved NMOS
line (two inverters per tap), biases it, and runs a transient.  A level-1 MOS
model is calibrated to sky130 core parameters from the PDK card (toxm =
4.148 nm -> Cox ~ 8.3 fF/um^2, u0 = 0.0896 m^2/Vs -> kp_n ~ 745 uA/V^2,
Vth ~ 0.45 V).  It is **not** the foundry BSIM4 card - see the tooling note
below - so treat the absolute numbers as +-30%.

| quantity | result |
|---|---|
| bias for 1.25 ns/tap | Vb = 0.67 V (sink W/L = 3/0.5) |
| tap spacing, 8 taps | 1.216 ns each, spread < 1 ps (identical stages) |
| span (reference to tap 8) | 9.73 ns (target 10 ns, -2.7%) |
| supply current | ~0.71 mA from 1.8 V |
| power, line only | 1.28 mW |
| area, line only | ~445 um^2 (8 x ~56 um^2; Rp, two devices, 50 fF) |
| open-loop span vs VDD -10% / +10% | 9.32 ns / 10.41 ns (-4% / +7%) |

Reading: a 1.25 ns tap is achievable in sky130 1.8 V at about 1.3 mW and
~445 um^2 (0.00045 mm^2) for the line, i.e. comfortably inside the 2 mW /
0.02 mm^2 targets once PD, charge pump and bias are added.  The open-loop
supply sensitivity is the risk: +-10% VDD moves the span by 4-7%, so the DLL
loop and a supply-referenced replica bias are mandatory, and high-frequency
supply noise still passes through.

What this run does **not** cover: foundry BSIM4 accuracy, tap mismatch
(identical stages assumed), jitter (no noise analysis), the phase detector /
charge pump / loop filter, lock acquisition, and the phase mux.

### 5.2 Closed-loop lock check (measured)

`verification/analog/dll_lock.py` closes the loop around the same 8-tap line:
a track-and-hold phase detector samples the last tap at ref + 10.3 ns, an
ideal charge pump (+-Icp) drives an R/C filter, and the filter voltage is the
line bias.  The PD/CP/filter are behavioural; the line is the SPICE line.
Default run (`make dll-lock`): 20 us transient, Icp = 5 uA, Rf = 100 k,
Cf = 100 pF, start 0.670 V.

| quantity | result |
|---|---|
| bias, settled | 0.6893 V (start 0.670 V) |
| bias drift, last 20% | 0.033 mV (tail ripple 0.017 mV) |
| line delay | 10.383 ns (sample instant 10.300) |
| held value at PD instant | 0.822 V (0.900 V = zero error) |
| static offset | +83 ps |
| phase lock, 50 ps | 3.80 us |
| bias settle, 1 mV | 7.16 us |
| converged | yes |

Reading: the loop converges and holds the tap edge constant (min = max over
the last 200 UIs).  The 83 ps offset is the **loop filter**, not the phase
detector.  `Rf` returns the filter node to the fixed pre-lock bias, so at lock
the charge pump must supply the 0.1925 uA drop across Rf; a mean CP current of
0.19 uA needs a held value of 0.830 V at the PD, and the simulation gives
0.822 V.  Doubling Rf halves the offset (measured 43 ps at 200 k).  This DC
path is a property of the first-order filter in this check, not of a real DLL
filter; the residual there would be the PD's own offset.  At 83 ps = 0.008 UI
it is far below one 1.25 ns tap and does not change the backend conclusion.

Two points about that number: the offset is a settling curve, not a constant
(193 ps at 2 us, 143 ps at 4 us, 93 ps at 8 us, 83 ps at 20 us), so a short
run does not reach the lock point; and it is not a PD sample delay.  The
default run is now 20 us, which is converged (drift 0.033 mV).

The lock check does not cover: PD/CP transistor design, supply/reference
noise, tap mismatch, or the phase mux.  It also does not prove a 10.000 ns
line delay - it proves the loop servos the tap's 0.9 V crossing to the PD's
sample instant (plus the filter offset above).

Tooling note: the installed sky130 PM3 cards use `.model name.N` binning plus
`agauss` mismatch terms; direct `.include` fails ngspice's model-name and
parameter checks, and `.lib sky130.lib.spice` blocks on an OSDI load in this
container (the repo's own `extract_lut.py --tech sky130` also times out).  The
level-1 model is the workaround, not a substitute for running the foundry card
on a working PDK install.

The phase mux must be glitch-free: switch only while both the old and the new
phase are low, and hold the selection for at least one period.  A phase step
during a sampling edge would corrupt a bit.

## 6. Integration status and remaining work

1. **RTL integration (done, external sampler).**  `CDR_MODE=4` now instantiates
   `cdr_multiphase` in `serdes_rx_digital.sv`.  The phase mux and sampler stay
   outside the digital netlist: the top exposes `rx_dll_tap[DLL_PH_W-1:0]`
   (3 bits by default) and consumes `rx_smp`, the bit sampled at that tap on
   the previous clk.  The wrappers (`serdes_digital_top`,
   `serdes_rx_axis_digital`, the 8b10b/64b66b tops, the SoC CDC wrapper) tie
   `rx_smp` low and leave `rx_dll_tap` open; they run modes 0-3 only.
   `tb/tb_rx_endpoint_dll.sv` runs the full endpoint with a behavioural
   `(tap+0.5)*1.25 ns` sampler at 0/+-700 ppm and pattern 3.
   Still needed for a chip: the DLL/mux/sampler macros, the muxed clock tree
   (8 generated clocks or one muxed clock) and a CDC/CTS review.
2. **Sampler setup.**  With tap 7 the sample is taken at 9.375 ns and read at
   10 ns - 0.625 ns of setup.  Either retime the sample one extra cycle (the
   loop already tolerates a cycle of latency) or restrict the usable tap range
   to 0..6.  Do this before claiming a 100 MHz sampler path.
3. **Transistor DLL.**  The line has a first-order SPICE check (§5.1: level-1
   model, 1.22 ns/tap, 1.28 mW, ~445 um^2, 9.7 ns span at Vb = 0.67 V) and a
   behavioural closed-loop lock check (§5.2: locks to 10.38 ns, 83 ps filter
   offset, 3.8 us).  There is still no transistor phase detector, charge pump
   or loop filter, no jitter/noise analysis, no tap-mismatch or Monte Carlo,
   no layout/PEX, and no run with the foundry BSIM card (the installed PM3
   cards cannot be loaded by ngspice in this container).
4. **Jitter tolerance.**  The TB drives ideal edges.  The DLL phase noise and
   the sampler aperture are not in the closed loop, so no JTOL claim follows
   from the BER sweeps above.
5. **Frequency resolution.**  `FREQ_FRAC=8` gives 122 ppm per LSB and is the
   committed value; the boundary-add pipeline costs ~2 LSB of extra dither
   (section 4).  `FREQ_FRAC=9` (61 ppm) was previously rejected because it put
   the *unpipelined* correction add at 10.27 ns.  With `step_ui_q` in place
   the correction is off the phase path, so 9 bits is worth re-trying if
   quieter integral state is needed; judge it by the routed A/B, not pre-PnR.

## 7. Reproduce

```bash
iverilog -g2012 -o build/tb_cdr_multiphase \\
    tb/tb_cdr_multiphase.sv rtl/cdr_multiphase.sv
vvp build/tb_cdr_multiphase +PPM=0
vvp build/tb_cdr_multiphase +PPM=1000 +PATTERN=3
# 100k-bit run: N_CHECK is a compile-time parameter
iverilog -g2012 -Ptb_cdr_multiphase.N_CHECK=100000 \\
    -o build/tb_cdr_mp_100k tb/tb_cdr_multiphase.sv rtl/cdr_multiphase.sv
vvp build/tb_cdr_mp_100k +PPM=-1000
```

Synthesis/STA recipe is the same as `bang_bang_pipeline_study.md` section 6
with `cdr_multiphase` as the top.  The analog checks are
`make dll-feasibility` and `make dll-lock`; both emit JSON under
`build/verify/`.

The routed A/B in section 3 is an open-source-flow measurement (`sv2v` +
LibreLane 3.0.14, `sky130A` / `sky130_fd_sc_hd`, `nom_ss_100C_1v60`,
10 ns): convert `rtl/cdr_multiphase.sv` with `sv2v`, then set
`DESIGN_NAME: cdr_multiphase`, `CLOCK_PORT: clk`, `CLOCK_PERIOD: 10`,
`PDK: sky130A`, `STD_CELL_LIBRARY: sky130_fd_sc_hd`,
`DEFAULT_CORNER`/`STA_CORNERS: nom_ss_100C_1v60`, and an SDC with
`create_clock -period 10 [get_ports clk]`, `set_clock_uncertainty 0.25 clk`,
2 ns input delay on `enable`/`flush`/`phase_offset`, 0.3 ns on
`smp`/`rst_n`, and 2 ns output delay.  The non-pipelined baseline is the same
file at commit `7cfaa76`.
