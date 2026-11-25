# OpenSERDES digital part: process-independent abstraction layer

This directory pulls the digital part of OpenSERDES out of the sky130 gate-level netlists / hard macros and turns it into synthesizable, parameterized RTL with no standard-cell binding.

> 中文版：[`README.zh-cn.md`](README.zh-cn.md)

## 0. Layout and migration

```text
rtl/            canonical digital RTL
rtl/compat/     legacy module-name adapters
tb/             self-checking simulations
constraints/    SDC templates
spec/           generator input specifications
scripts/        generator, regression and qualification gates
tech/           per-PDK profiles, arch_db.yaml and PDK_ROOT/PDK_LIB_ROOT setup
               (see tech/README.md)
docs/           migration, clocking, pre-PnR STA, CDR/RX study,
               generator, qualification, standard-cell library selection,
               library catalog, timing architecture, PVT/PEX/MC status
legacy/rtl/     original serializer/deserializer sources, read-only
legacy/analog/  original analog/layout evidence, read-only
```

The original `Serializer/` and `DeSerializer/` keep their GDS/SPICE/LEF/PDF evidence. The old analog/physical directories `Inverter_Based_Tx/`, `OverSampling_CDR/`, `Resistive_FB_inverter/` and `Receiver_Bypassing_CDR/` have moved to [`legacy/analog/`](legacy/analog/README.md) as read-only provenance. The new digital flow uses only `rtl/`. The full mapping, compatibility adapters and remaining gaps are in [`docs/migration.md`](docs/migration.md).

## 1. Design principles

- **No process cell names anywhere**: no `sky130_fd_sc_hd__*`, `sg13g2_*`, `ics55_*`.
- **Single clock**: all sequential logic is driven only by `clk`; progress is controlled by `enable`.
- **No generated clocks**: `READY` and `COMPLETE` are no longer used as clocks.
- **Registered inputs/outputs**: parallel data is sampled only on `load && ready`; outputs are registered on completion.
- **Parameterized arrays**: `DATA_WIDTH`, `WORDS`, `LSB_FIRST`; `par_data` is `[WORDS-1:0][DATA_WIDTH-1:0]`, and bit/word selection is built with generate loops.
- **Consistent reset**: all leaf blocks use asynchronous active-low `rst_n`; at the top, each clock domain goes through `reset_sync` (asynchronous assert, synchronous release).
- **Explicit handshakes**: `ready/load`, `serial_valid`, `par_valid`, `busy`; plus a word-streaming valid/ready architecture.
- **Pluggable CDR**: three modes, `fixed_oversampling`, `adaptive_oversampling` and `source_sync`, chosen by default by the generator from ppm/clocking/rate.
- **Explicit frame sync**: `comma_align` supports one-shot and per-frame realignment, and outputs `rx_frame_locked`, `rx_lock_stale` and the recovered serial stream.
- **Verification levels**: `RTL_FUNCTIONAL`, `PRE_PNR_TIMING`, `POST_ROUTE_TIMING`, `PHYSICAL_SIGNOFF`, `LINK_VALIDATED`; evidence from different levels must not be mixed.
- **The CDR is an abstract replacement**: the original repository only has a gate-level `CLK_RECOVERY`; this directory provides a synthesizable oversampling CDR. It is not a cycle-by-cycle clone of the original gate-level netlist.

## 2. Modules

### `rtl/serializer.sv`

```
serializer #(
  DATA_WIDTH = 32,
  WORDS      = 8,
  LSB_FIRST  = 1
) (
  clk, rst_n, enable, load, par_data,
  ready, serial_out, serial_valid, busy, frame_done
);
```

- `load && ready` samples `par_data`.
- Each `enable` outputs one serial bit.
- `serial_valid` is high within a frame; `frame_done` pulses on the cycle after the last bit is accepted.
- Bit order:
  - `LSB_FIRST=1`: word0 bit0 → word0 bit31 → word1 bit0 ...
  - `LSB_FIRST=0`: word0 bit31 → word0 bit0 → word1 bit31 ...
- `CAPTURE_MODE=1`: capture one word per cycle over `WORDS` cycles, cutting the single-net fanout of the 256 input-register enables down to 32; the cost is `ready` low for `WORDS` cycles, and external data must stay stable.
  The generator selects it with `--capture-mode`. Measured (one-hot capture): sky130 10 ns +2.78 ns;
  IHP 10 ns +5.66 ns; H7CL 10 ns +7.21 ns; H7CR 10 ns +6.39 ns;
  H7CH 20 ns +13.15 ns; GF180 5 V 40 ns +22.50 ns.
  Cross-process endpoints: H7CL TX 5 ns +1.03 ns, H7CL RX 2.5 ns (OS=2) +0.315 ns;
  H7CL top 10 ns TX + OS=2 is **+2.82 ns (100 MHz link)**;
  H7CL top 5 ns TX + OS=2 is **+0.315 ns (200 MHz link)**.
  GF180 5 V: serializer 20 ns +2.50 ns, deserializer 20 ns +8.14 ns;
  but top 20 ns + OS=2 (RX 10 ns) is −17.13 ns, so a 20 ns full link needs a source-synchronous RX.

### `rtl/deserializer.sv`

```
deserializer #(
  DATA_WIDTH = 32,
  WORDS      = 8,
  LSB_FIRST  = 1
) (
  clk, rst_n, enable, serial_in, serial_valid,
  par_data, par_valid, busy
);
```

- `serial_valid && enable` receives one serial bit.
- When `align` is high, the bit/word counters reset; the next valid bit becomes word0 bit0. This is the hook for comma/training alignment.
- Each word is held by one generate instance of `deserializer_word`; `par_data` comes directly from those word registers, **with no `INT_PAR_IN` shadow register and no second-stage `PAR_IN` latch**.
- When a frame completes, `par_data` is already in word/bit order and `par_valid` pulses for one cycle.
- Word/bit selection uses one-hot shift registers, so the capture path has no binary comparator; H7CL 2.5 ns RX +0.315 ns.
- No `COMPLETE` generated clock.

### `rtl/deserializer_word.sv`

One `DATA_WIDTH` register bank per word; `capture` is active only for the selected word and when `serial_valid`. `par_data` maps directly to these word registers, fixing the original DeSer's `INT_PAR_IN` propagation problem.

### `rtl/serdes_soc_wrapper.sv`

Single-clock SoC reference top: APB CSR + TX AXIS + source-sync RX AXIS. APB map: reg0 control, reg1..6 scratch/reserved, reg7 status, reg8 BIST_CTRL, reg10/11 pad BIST counters, reg12 BIST_STATUS, reg13/14 word BIST error/frame counters. A TX AXIS frame is comma + `WORDS` payload words; an RX AXIS frame is `WORDS` payload words; `ALIGN_MODE=2` realigns on the comma every frame. `BIST_CTRL[0]` + `[6]=0` is pad-level mode: PRBS drives `tx_serial_out` directly and bypasses the core. `BIST_CTRL[6]=1` is word/datapath mode: a PRBS frame source replaces the AXIS input and the RX AXIS output is consumed by a checker, going through the full adapter/serializer/RX core. `BIST_CTRL[4]` selects near-end loopback. Pad mode internally pulls the TX/RX core enables low; counters are read through the request/acknowledge handshake of `bist_snapshot` to avoid tearing. This wrapper is for single-clock integration acceptance; `spec/soc_single_clock.yaml` is an example.

### `rtl/serdes_soc_cdc_wrapper.sv`

Multi-clock SoC reference top: `pclk`/`s_axis_aclk`/`m_axis_aclk`/`tx_clk`/`rx_clk` are separate; TX/RX AXIS each go through an `async_fifo`, and APB control/status go through `cdc_sync` 2FF synchronizers. The link is still source-sync (`OVERSAMPLE=1`, comma per frame), but the AXIS/APB clocks can differ from the link clock. The BIST PRBS generator/checker sit in the `tx_clk` domain (a continuous bit stream cannot cross clocks); control bits are `cdc_sync`'d bit by bit, and counters return to `pclk` through the `bist_snapshot` stable-bus handshake. Checking an external (non-loopback) `rx_serial_in` requires `tx_clk`/`rx_clk` to be the same frequency and phase; loopback mode is the deterministic self-test mode. `spec/soc.yaml` generates this wrapper.

### `rtl/serdes_bist.sv`

PRBS BIST wrapper: `prbs_gen` + `prbs_check` share `start/clear` and the seed; it outputs `serial_out`, checks `serial_in`, and exposes `error/error_count/bit_count`. Both SoC wrappers already integrate this primitive over APB: `BIST_CTRL` controls enable/start/clear/inject/loopback/snapshot, `BIST_ERROR_COUNT`/`BIST_BIT_COUNT` are snapshot read-only registers, and `BIST_STATUS` summarizes error/running/valid/width/word_mode. It is a pad-level bit-stream BIST that bypasses the serializer/deserializer; datapath-level verification is provided by the word BIST below.

### `rtl/prbs_word_gen.sv` / `rtl/prbs_word_check.sv`

Word/datapath-level PRBS: the generator emits comma + `WORDS` PRBS words per frame (each word is `DATA_WIDTH` consecutive PRBS bits, restarting from the seed after the comma); the checker compares word by word per AXIS beat and closes the frame on `tlast`. Together they form an "AXIS-level BIST frame source/sink", enabled in the single-clock SoC wrapper through `BIST_CTRL[6]`, covering `axis_*_adapter`, the serializer, the RX core and comma alignment. The error count is a bit-error count, plus a frame count.

### `rtl/prbs_gen.sv` / `rtl/prbs_check.sv`

PRBS7/15/31 serial generator/checker with sticky `error`, `error_count` and `bit_count`. A BIST/link-test primitive; the `soc`/`phy_ip` packages bundle it together with the BIST wrapper (the soc wrapper already depends on it directly).

### `rtl/axis_tx_adapter.sv` / `rtl/axis_rx_adapter.sv`

### `rtl/serializer_half_rate.sv`

Half-rate TX prototype: `clk_word` handles capture/handshake and `clk_bit = 2*clk_word` outputs bit by bit;
request/acknowledge crosses the two clock domains. Bit-exact against `serializer_stream` (8x4/16x4/
32x8/5x3, LSB/MSB, 40 frames). Worst slack at a sky130 HD TT 5 ns bit clock is
**0.658 ns** (baseline 0.406 ns, about +0.25 ns). Still a prototype, not wired into the SoC/generator;
`enable` and the full RX half-rate state machine are still to be done.

### `rtl/receiver_pair_sampler.sv`

Full-rate sampler boundary for the half-rate RX prototype: the bit clock samples a 2-bit
history and, on the word clock, outputs `bit0` (earlier) / `bit1` (newer) and `pair_valid`;
`tb_receiver_pair_sampler` checks against a reference history. It contains no CDR, comma alignment
or full frame-valid semantics.

### `rtl/deserializer_half_rate.sv`

Parallel bank of the half-rate RX prototype: each `clk_word` receives 2 bits (bit0 first, bit1 second);
the word/bit counters and `par_valid` run on the word clock. The DDR/2-phase sampler, CDR and
comma alignment are not in this prototype. `tb_deserializer_half_rate` covers 8x4/16x4/32x8,
LSB/MSB, 50 frames. Worst slack at a sky130 HD TT 10 ns word clock is 6.866 ns
(the full-rate deserializer at 5 ns is 2.843 ns; the periods differ, so this is not an end-to-end RX conclusion).

### `rtl/serializer_hier_2to1.sv`

A bit-exact 2:1 hierarchical word-select-tree prototype of `serializer_stream`: same interface/handshake/output timing,
using a balanced 2:1 mux tree instead of a flat WORDS-way one-hot mux. Passes the comparison TB for 8x4/32x8/5x3,
LSB/MSB and random backpressure. A first Yosys/ABC comparison at sky130 HD 5 ns gives the same
worst slack as the baseline (0.406 ns), showing that ABC does not preserve a purely combinational mux tree
as a shorter path; registered tree stages / half-rate are needed to actually change the critical path.
See [`docs/timing_optimization.md`](docs/timing_optimization.md).

AXIS word adapters: one beat is one `DATA_WIDTH` word, and `tlast` marks the last word. The TX adapter drives `serializer_stream`'s `in_valid/in_ready/in_word`; the RX adapter splits the parallel frame of `rx_frame_valid` into AXIS beats and asserts `frame_overflow` if the previous frame has not drained.

`serdes_tx_axis_digital` / `serdes_rx_axis_digital` are AXIS endpoint wrappers; the AXIS clock is the same as `tx_clk`/`rx_clk`, so crossing clock domains needs an external AXIS async FIFO.

### `rtl/apb_csr.sv` / `rtl/async_fifo.sv`

`apb_csr` is a word-aligned APB control/status register bank: by default the last address is read-only `status_i`; `STATUS_IDX` moves the status register, and `RO_MASK`/`ro_i` add arbitrary read-only registers (the BIST counters use this mechanism); writes to read-only addresses are ignored, and non-word-aligned or out-of-range accesses return `pslverr`. It is the configuration interface for the `soc`/`phy_ip` presets; it is currently a standalone primitive, not yet mandatory in the digital top.

`async_fifo` is a dual-clock Gray-pointer FIFO; `DEPTH` must be a power of two, and small depths are built from register arrays. It can serve as the CDC primitive from the RX recovered-clock domain to the local SoC clock domain, but CDC cleanliness still needs the target tool's CDC check and cannot be self-proven by the RTL.

### `rtl/link_training_fsm.sv` / `rtl/crc_frame.sv`

`link_training_fsm` is a standalone digital training state machine: it sweeps the phase axis first, then the threshold axis, takes differences of a monotonic error counter within each candidate's dwell window, and freezes the best setting; while locked it keeps monitoring error growth and automatically retrains above a threshold. The outputs `phase_sel`/`thresh_sel` are only **actuator requests**, and `phase_we`/`thresh_we` are single-cycle load pulses. The current CDR has no external phase/threshold input, so this FSM is **not yet wired into** `serdes_digital_top`; do not treat it as verified link training.

`crc_frame` is a parameterized serial CRC primitive, verified for CRC-16/CCITT-FALSE (`0x29B1`) and CRC-32/ISO-HDLC (`0xCBF43926`). It only accumulates CRC; it does not define AXIS framing, sync words, sequence IDs or retransmission, and it is not yet wired into the generated package's frame path.

Capability boundaries and integration conditions for training/CRC are in `docs/digital_training_crc.md`.

### `rtl/axis_crc_inserter.sv` / `rtl/axis_crc_checker.sv`

AXIS CRC insert/check primitives. The inserter appends CRC beats after the payload, marks the last payload beat with `m_axis_tuser`, and leaves `m_axis_tlast` for the last CRC beat; the checker forwards the payload, consumes the CRC beats, and outputs `crc_ok` / `crc_error`. `serdes_tx_axis_digital` / `serdes_rx_axis_digital` can optionally enable this path through `CRC_ENABLE`, adding `ceil(CRC_WIDTH/DATA_WIDTH)` words to the serialized frame; the SoC async wrapper does not expose CRC CSRs yet.

### `rtl/encoder_8b10b.sv` / `rtl/decoder_8b10b.sv`

8b/10b encoder/decoder, verified for all 256 data bytes and a K28.5 round trip. The implementation is adapted from the MIT-licensed `libsv`; source and license are in `docs/third_party.md`. `pcs_8b10b_tx/rx` provide PCS wrappers with valid/error; `serdes_8b10b_top` encodes an 8-bit payload into a 10-bit line word, and the generator accepts `coding: 8b10b` when `payload_width: 8`, `target: link`, `align: external`.

### `rtl/pcs_64b66b.sv`

64b/66b primitive: 2-bit sync header + 64-bit self-synchronous scrambled payload, supporting data/control block headers, rejection of illegal sync, and a 66-to-64-bit gearbox. `serdes_64b66b_top` encodes a 64-bit payload into a 66-bit line word; the generator accepts `coding: 64b66b` when `payload_width: 64`, `target: link`, `align: external`. With `RX_GEARBOX=1`, the RX side uses `pcs_64b66b_rx_gearbox` to lock blocks from the 1-bit serial stream with a 66-bit sliding window, and `pcs_64b66b_ctrl_decode` distinguishes pure-data / all-control / mixed start/terminate blocks (`rx_ctrl_kind`/`rx_block_type`); this mode is restricted to `WORDS==1`, and TX inserts IEEE all-control idle blocks between frames to preserve block boundaries. Full control-field extraction and lane distribution remain separate PCS work. See `docs/pcs_coding.md`.

### Protocol PHY base layer (towards USB/PCIe)

After three rounds of external research (LiteICLink, the LitePCIe soft PHY and PR #250, USB3-PIPE, LiteEth basex), the "SerDes as a USB/PCIe PHY base layer" pieces were added: `rtl/link_watchdog.sv` ("still trying" vs "stuck"), `rtl/saturating_counter.sv` (saturating, non-wrapping bit-error/link-drop counters), `rtl/clock_cycles.sv` (Gray counter across domains, measured ppm checked against the phase model), `rtl/tx_ctrl_align.sv` (aligning control inputs with in-flight data, PR #250's `tx_idle_delay`); `pcs_8b10b_rx_stream` gained `SUBSTITUTE_ERROR` (replace decode/disparity errors with K28.4 and report via charisk, PCIe 6.3.1.4), and `pcs_8b10b_comma` gained a search watchdog wired through `serdes_rx_digital`; the SoC wrapper gained CSR16–19 (bit-error count, link-drop count, TX/RX cycles) and `CSR0[5]/[6]`, `CSR7[8..10]`. See `docs/phy_base_layer.md`.

### Bus adaptation for SoC integration

The data plane of `serdes_soc_cdc_wrapper` is AXI4-Stream and the control plane is APB (CSR0 control, CSR7 status, CSR8–12 BIST, CSR13–15 training/CRC, CSR16–19 link health and cycle measurement). With `PKT_ENABLE=1`, the Packetizer is wired into the `s_axis` front end to support variable-length frames.

A Wishbone SoC connects through two adapters, **without duplicating any registers**: `rtl/wb_apb_bridge.sv` (Wishbone B4 slave → APB master, accessing the same CSRs, with error paths for out-of-range / unaligned / partial `sel`; rejected requests never reach the register bank) and `rtl/wb_axis_fifo.sv` (Wishbone slave ↔ AXIS: writes push TX words, reads pop RX words, FIFO full/empty are reported as `wb_err` rather than silently dropped, and `tx_flush` can discard queued TX words). Both are single-clock; crossing into the SerDes word clock is handled by the `async_fifo` already in the wrapper. See `docs/soc_streaming.md`.

### Jitter/phase reference model

`verification/channel/phase_model.py` and `tb/jitter_clock.sv` are **two implementations of the same model** using exactly the same integer arithmetic (16-bit xorshift, Irwin-Hall Gaussian substitute, 64-entry Q1.15 sine table, truncated ppm ramp), so the RTL and Python TIE sequences match bit for bit. `tb/tb_phase_model.sv` reads vectors written by Python and compares edge by edge; `make phase-model-rtl` and 4 `phase_model_*` regression cases guard this equivalence. Supports four axes, RJ / SJ / ppm / DCD, with 6 configurations covered. Boundaries: it is not a channel model (no ISI/reflections/CTLE-DFE/SSC), the Gaussian is a bounded ±6σ substitute, and `tj_at_ber` is a dual-Dirac budget, not a measured BER. See `docs/phase_model.md`.

### Multi-lane and link layer

`rtl/serdes_multilane_top.sv` combines several source-sync / bang-bang lanes (each lane one `serializer_stream` + `serdes_rx_digital` + `async_fifo`) into one word stream: TX sends in the same word slot on all lanes, inserts a marker carrying an epoch every `MARKER_PERIOD` payload words, and sends idle when there is nothing to send. On the RX side, `rtl/lane_deskew.sv` absorbs whole-word inter-lane skew with per-lane elastic buffers and releases in lockstep from marker boundaries; the output is payload only (`marker_ok` pulses once every `MARKER_PERIOD` output words). Parameter constraints `MARKER_PERIOD > MAX_SKEW` and `DESKEW_DEPTH >= 2*MARKER_PERIOD + MAX_SKEW` (power of two) fail closed at elaboration; when `CDR_MODE != 2`, `OVERSAMPLE >= 4` and `CDR_BYPASS=0` are required. On the SoC side, `axis_lane_bonder`/`axis_lane_debonder` in `rtl/axis_lane_bonder.sv` convert between wide AXIS (`LANES*DATA_WIDTH`) and per-lane words; the debonder is gated by `link_ok` and counts dropped beats in `underflow`. `preset: multilane` generates this package directly (`spec/multilane.yaml`, 2/4/8 lanes). See `docs/multilane.md`.

`rtl/axis_frame_packetizer.sv` slices variable-length AXIS frames into the fixed `WORDS`-beat frames required by `axis_tx_adapter` (the header beat carries `last_frag`/`frag_len`), and `axis_frame_depacketizer` reassembles them; `rtl/axis_ordered_set_insert.sv` puts the `comma` ordered set back in front of each fragment chain. With `PKT_ENABLE=1`, `serdes_soc_cdc_wrapper` places this layer at the `s_axis` front end / `m_axis` back end, so the SoC side can send and receive frames of any length (Ethernet, AXI bursts) directly; `tb_soc_cdc_packetizer` verifies 5 frames of 36 bytes end to end, byte for byte. See `docs/soc_streaming.md`. `rtl/axis_reliable_link.sv` provides go-back-N reliable transport: `axis_crc_inserter/checker` (`STORE_AND_FORWARD=1`) + a minimal Sequence ID + cumulative ACK/NAK; bad frames are dropped, duplicate frames are re-ACKed, timeout/NAK cause a rollback, and exceeding `RETRY_LIMIT` sets a sticky `link_error`. Both are protocol logic; `tlast`/`tuser` are sideband, so connecting to a serial link needs in-band frame boundaries. See `docs/link_layers.md`.

### `rtl/receiver_sampler.sv`

1-bit sampling model of the receiver's digital boundary: it models only the `q <= d` behavior of the 2 GHz NAND DFF in the original `Receiver.sp`, not the resistive-feedback inverter, the CMOS inverter or analog setup/hold.

### `rtl/reset_sync.sv`

Reset synchronizer with asynchronous assert and synchronous release. The top `serdes_digital_top` instantiates one for each of the TX/RX clock domains.

### `rtl/serdes_tx_digital.sv` / `rtl/serdes_rx_digital.sv`

Independent TX/RX endpoint wrappers. The two can be synthesized separately with different PDKs and then combined in the same link; the protocol/RTL is unchanged, only the target library and analog wrapper change.

`CDR_MODE` of `serdes_rx_digital` selects the CDR: `0=fixed_oversampling`, `1=adaptive_oversampling`, `2=source_sync`, `3=bang_bang`, `4=dll_phase`; the old `CDR_BYPASS=1` is equivalent to `CDR_MODE=2`. In mode 4 the phase selection/sampler is outside the digital netlist: the top outputs `rx_dll_tap` and takes `rx_smp` as input (see `docs/dll_backend.md`). In source-sync mode the external `rx_clk` is the bit clock and goes through `receiver_sampler` straight into the deserializer; then `rx_clk_out=rx_clk`, `rx_lock=1`, and `OVERSAMPLE` does not apply.

`ALIGN_MODE`: `0=external rx_align`, `1=comma one-shot alignment`, `2=comma realignment every frame`. New outputs `rx_frame_locked`, `rx_frame_valid` (= `rx_par_valid & rx_frame_locked`), `rx_serial_data`, `rx_serial_valid` and `rx_freq_trim` are for link scoreboards, state recovery and calibration observation.

### `rtl/serdes_digital_top.sv`

Digital top made of `serdes_tx_digital` + `serdes_rx_digital`. TX/RX clocks are independent; `rx_clk_out` is for monitoring only, and `rx_sample_strobe` is the data-valid strobe. `rx_frame_locked` means comma/training frame alignment is established; `rx_lock_stale` only means no edges for a long time and should not be used alone to stop receiving. With `TRAINING_ENABLE=1`, `train_error_count` drives `link_training_fsm`, whose `phase_sel` connects to the CDR's `phase_offset`. `serdes_soc_cdc_wrapper` also provides rx_clk-domain training and APB CSR 13/14 telemetry (best_errors, locked/busy/retrain/phase); the error source is still an external scoreboard/PRBS checker, the generator switch is not wired up yet, and it is off by default.

### `rtl/cdr_sync.sv` / `rtl/cdr_oversampling.sv` / `rtl/cdr_adaptive.sv`

The CDR is split into three explicit stages:

```text
raw data -> cdr_sync (STAGES flops) -> edge detect + data select
         -> phase counter -> sample_strobe -> deserializer
```

`samples[0]` of `cdr_sync` is the newest sample and `samples[STAGES-1]` the oldest, settled sample. Edge detection and **data selection both use the old sample**, so increasing `SYNC_STAGES` really increases the metastability settling time of the data path, not just the edge-detect path. The default is `SYNC_STAGES=3`; MTBF still needs to be confirmed on the target PDK, making sure the synchronizer registers are not optimized away or merged.

```
cdr_oversampling #(
  OVERSAMPLE = 4, SYNC_STAGES = 3,
  LOCK_THRESH = 8, LOCK_STALE_BITS = 16
) (
  clk, rst_n, enable, flush, data_in,
  data_out, sample_strobe, clk_out, lock, lock_stale
);
```

- `fixed_oversampling`: during acquisition, every edge loads 0 into the phase counter; after lock it runs freely. It does not track frequency offset.
- `sample_strobe` pulses once per bit and is the **data-valid strobe**; use it, not `clk_out`, to receive `data_out`.
- `lock` asserts after `LOCK_THRESH` consecutive edges are detected; a long absence of edges only sets `lock_stale` and **does not** pull lock low through data-pattern-dependent decay.
- `flush` clears lock/phase state, for protocol recovery.
- Constraint: `OVERSAMPLE ≥ 4`; `OVERSAMPLE=2` is a restricted mode, and the generator must have `allow_os2: true` explicitly.

```
cdr_adaptive #(
  OVERSAMPLE = 8, SYNC_STAGES = 3,
  FRAC_BITS = 8, LOOP_GAIN = 4,
  LOCK_THRESH = 8, LOCK_RELEASE = 8,
  LOCK_STALE_BITS = 32, PHASE_DEADBAND = 1,
  LOOP_SETTLE_BITS = 32
) (
  clk, rst_n, enable, flush, data_in,
  data_out, sample_strobe, clk_out, lock, lock_stale, freq_trim
);
```

- `adaptive_oversampling`: uses fractional period control to occasionally insert/delete one sample period, tracking the TX/RX ppm offset; `freq_trim` is the signed loop state.
- After lock there is a `LOOP_SETTLE_BITS` window that keeps acquisition transients from pushing the sampling phase off; a deadband filters the 1/OVERSAMPLE UI quantization jitter.
- This is a **first-order frequency-tracking loop**, not a second-order jitter-filtering PLL. `OVERSAMPLE ≥ 8` is recommended; high-jitter links should use a phase-interpolator/PLL CDR.
- Verified at `PPM = 0, +700, -700` and `100 ppm`, 5000 bits recovered continuously without bit slip (`tb_cdr_adaptive`, `FRAC_BITS=12`). **Limitations**: jitter tolerance, gate-level equivalence and the analog front end are not verified; production high-jitter links should still use a second-order loop or a phase interpolator.

### `rtl/comma_align.sv`

Searches the recovered serial stream for `SYNC_PATTERN` (default 8'hBC). A match occurs on the last bit of the pattern; that bit is dropped by the deserializer's `align` priority, and the next valid bit becomes word0 bit0. With `ALIGN_MODE=2`, it re-arms after every `rx_par_valid`, supporting continuous or gapped per-frame realignment. `flush` or loss of CDR lock clears `rx_frame_locked`.

## 3. Key differences from the original OpenSERDES implementation

| Original implementation | Abstraction layer |
|---|---|
| Serializer depends on the `READY` edge; `int_PAR` is multiply driven and removed by synthesis | Single `clk` + `load/ready`; input registers are really kept |
| Deserializer's 256 DFFs are clocked directly by `COMPLETE` | Single `clk` + `enable`, no generated clock |
| Deserializer's `4'd3` branch is missing `OUT[0] <= SERIAL_IN` | Indexed bit writes, no state-3 bit slip |
| 300 parallel outputs exposed directly | Parameterized `WORDS×DATA_WIDTH` array; the generator can choose parallel/serial configurations |
| DeSer `INT_PAR_IN` not propagated correctly | One register bank per word, `par_data` connected directly, no shadow latch |
| CDR exists only as a gate-level netlist; 22-bit SCAN_IN semantics undocumented | Three synthesizable modes (fixed/adaptive/source-sync); explicit `sample_strobe/lock/lock_stale/freq_trim` contract; `comma_align` frame sync |
| Original Serializer has only 13 DFFs (input registers removed), DeSer more | After abstraction: Serializer 266 FF, DeSer 266 FF, Top 550 FF; single clock + `enable`, must go through CTS; see `docs/clocking.md` |
| No testbench | Self-checking TBs in the `Makefile`'s `SIM_BINS` and `tb/` (serializer LSB/MSB/capture/stream, deserializer LSB/MSB, loopback LSB/MSB, 32×8, link scoreboard, channel, fixed/adaptive CDR, 4x bang-bang CDR, phase_offset 0..3 sweep, generated-endpoint bang-bang, frame sync, CDR bypass, compat legacy/port-order, APB CSR, async FIFO, AXIS TX/RX adapter + endpoint, PRBS7/15/31, pad BIST7/15/31, word BIST, single/multi-clock SoC wrappers) |

## 4. Verification

```bash
make sim      # iverilog: all self-checking TBs (list in Makefile SIM_BINS)
make regress  # parameter matrix + link/scoreboard + generator package checks
make lint     # Verilator -Wall (0 warnings)
make synth    # Yosys generic gate-level check (converted to Verilog first with sv2v v0.0.12)
make gen SPEC=spec/default.yaml OUT=out/default   # generates rtl/sv2v/*.v inside the package for LibreLane/Yosys
make qualify REPORT=signoff.json LEVEL=PRE_PNR_TIMING
make signoff PACKAGE=out/default REPORTS=out/default/reports SIGNOFF_LEVEL=POST_ROUTE_TIMING
make verify-link VERIFY_PACKAGE=out/soc  # bound SI/BER + RTL JTOL gate -> reports/ber.json
make ppa-import PPA_CSV=measured.csv PPA_OUT=tech/ppa_measured.yaml
```

Verified:

- `tb_serializer` LSB/MSB/capture: PASS
- `tb_serializer_stream`: PASS (per-word valid/ready, back-to-back frames)
- `tb_deserializer` LSB/MSB: PASS
- `tb_serdes_loopback` LSB/MSB: PASS
- `tb_serdes_default` 32×8: PASS
- `tb_serdes_channel`: real `rx_serial_data` serial scoreboard + `rx_par_data` parallel frame scoreboard; covers comma per-frame alignment, inter-frame gaps, relock after `rx_flush`: PASS
- `tb_channel` delay/invert/error injection: PASS
- `tb_cdr`: fixed CDR, PRBS7, OS=4 and OS=2, 200 recovered bits each compared bit by bit with the transmitted sequence: PASS
- `tb_cdr_adaptive`: 0 / +700 / -700 / 100 ppm, 5000 bits recovered continuously: PASS
- `tb_frame_sync`: comma, gap, mid-frame flush/re-acquire: PASS
- `tb_cdr_bypass`: source-sync receiver-only: PASS
- `tb_compat_legacy` / `tb_compat_port_order`: named and positional legacy compatibility: PASS
- Verilator lint: 0 warnings
- Yosys synth: generic gates/DFFs only, no latches, no memories
- `scripts/regress.py`: regression matrix (case count computed dynamically by the script; this run 72/72), including the parameter matrix, link scoreboard, APB/FIFO, AXIS endpoint, PRBS/BIST, single/multi-clock SoC wrappers, generator package, signoff collector and measured-PPA gate

SI/BER verification framework (`verification/`, pure Python + optional iverilog):

```bash
pip install -r verification/requirements.txt
python -m verification.cli eye     --profile verification/profiles/stressed.yaml --out build/verify/eye.png
python -m verification.cli bathtub --profile verification/profiles/stressed.yaml --out build/verify/bathtub.png
python -m verification.cli eye --profile verification/profiles/stressed.yaml --channel-file channel.s4p --channel-port-map tx_p=0,tx_n=1,rx_p=2,rx_n=3 --out build/verify/eye_touch.png
python -m verification.cli jtol    --profile verification/profiles/stressed.yaml --out build/verify/jtol.json --model analytic
python -m verification.rtl.runner  --nbits 800 --sj-peak-ui 0.2 --sj-freq-hz 500000   # real cdr_adaptive
python -m verification.rtl.runner  --nbits 600 --chain rx --noise-rms-v 0.12      # full serdes_rx_digital + analog channel slicer
make verify-link VERIFY_PACKAGE=out/soc PYTHON=/usr/bin/python3                            # capability gate -> reports/ber.json
make verify-link VERIFY_PACKAGE=out/soc VERIFY_ANALOG_LUT=build/verify/sky130_lut.json  # bound package + sky130 LUT
make analog-lut ANALOG_TECH=sky130 PYTHON=/usr/bin/python3                                  # tech-config driven; auto-discovers sky130 PM3 models in ~/.ciel/ciel
make analog-tech-check ANALOG_TECH=sky130 PYTHON=/usr/bin/python3                           # print model/Magic/KLayout resolution
make sky130-pvt PYTHON=/usr/bin/python3                                                    # 45-point corner x V x T LUT
make sky130-mc PYTHON=/usr/bin/python3 MC_SAMPLES=20                                       # behavioral mismatch MC (not PDK MC)
make pex-compare SCHEM_LUT=... PEX_LUT=...                                                # PEX vs schematic LUT delta
make analog-lut-demo PYTHON=/usr/bin/python3                                                # generic self-check without a PDK (not sky130)
```

`make verify-link VERIFY_PACKAGE=<out>` reads the line rate, DATA_WIDTH, WORDS, SYNC_LEN and design/interface from the package manifest and rejects unbound or inconsistent claims; on success it writes the eye/bathtub/JTOL results to `reports/ber.json` and writes back `link_capability`. `--chain frame` (the default) uses the actually generated frame width for the `rx_par_data` scoreboard. `verification/analog/` provides tech-config-driven schematic-level LUT extraction (ngspice + PDK models, no PEX; sky130 supported, gf180/ihp are scaffolds); `scripts/verify_link.py --analog-lut ... --tx-load-ff ...` can replace the profile value with an RC tau derived from the TX rise time. PVT/PEX are still outside the claimed scope.

`verification/` separates three kinds of questions: 2-UI eye / eye metrics, empirical BER bathtubs from fixed-phase or fixed-threshold sweeps (zero errors report only a 3/N upper bound, never BER=0), and Gaussian-tail statistical BER. `jtol --model analytic` is an explicitly labeled fixed-phase demo without a CDR; `verification.rtl.runner` drives the real RTL with iverilog: `--chain cdr` drives `cdr_adaptive` directly, `--chain rx` feeds the bit stream sliced from the Python analog channel (ISI + AWGN + threshold) into the full `serdes_rx_digital` chain, and `--chain frame` adds the comma frame format and uses `rx_par_data`/`rx_frame_valid` for a frame-level scoreboard. It counts BER, bit slips and lock losses on the actually recovered bits/frames. It is still not an analog TX/RX, PEX or transistor-level model; see `verification/README.md`.

CI (`.github/workflows/ci.yml`) installs a pinned sv2v on every push/PR, generates `out/ci-soc`, runs `make sim lint synth regress`, the framework tests and `make verify-link` on the bound package, and uploads `ber.json`; a missing PyYAML, iverilog or sv2v, or any failing gate, fails CI. Native SystemVerilog is still used for iverilog/Verilator; Yosys/LibreLane read only `rtl/sv2v/*.v`.

Top/channel/SDC:

- `rtl/serdes_digital_top.sv`: digital top
- `tb/channel_model.sv`: digital channel model with delay/inversion/deterministic error injection (no analog loss/ISI)
- `constraints/serdes_digital.sdc`: TX/RX dual clocks, asynchronous clock groups, uncertainty, I/O delay template (template values are not signed off with target-PDK tools; the generator must replace period/uncertainty)
- `scripts/gen_ip.py`: spec → self-contained package (RTL/filelist/params/3-level SDC/PnR config/manifest)
- `scripts/qualify.py`: checks evidence along `RTL_FUNCTIONAL → ... → LINK_VALIDATED` and rejects cross-level claims
- `docs/clocking.md`: clock-tree audit of the original netlist and skew budget (corrected for 3-level CTS / balanced fanout)
- `docs/prepnr_sta.md`: pre-PnR STA methodology, reset/control separation, three process tiers and wire-delay budget
- `tech/<pdk>/tech.yaml`: PDK/SCL/LIB/corner/CTS targets; paths expand `${PDK_ROOT}` / `${PDK_LIB_ROOT}`, see `tech/README.md`
- `scripts/analyze_clock_tree.py`: extracts clock-tree depth/fanout from `.lvs.v`

## 5. Parameters and target processes

`DATA_WIDTH`/`WORDS` are not tied to a process; `RATIO=DATA_WIDTH*WORDS` is derived inside the modules to avoid inconsistent parameters. The target process is selected by `limits` in `tech/<pdk>/tech.yaml`; the actual fields are:

```yaml
limits:
  class: MS
  default_period_ns: 40.0
  serial_mbps: 25
  max_endpoint_line_rate_mbps: 100
  max_link_line_rate_mbps: 50
```

These gates come from pre-PnR measurements; they are conservative values, not signoff. The generator picks the link or endpoint gate by `target=link/tx/rx` and rejects configurations beyond the limits; `--relax-limits` is only for explicit experiments.

The generator no longer looks up "process name → fixed architecture"; instead it:

1. reads candidate architectures and relative area/power/latency from `tech/arch_db.yaml`;
2. filters by target, RATIO, line rate, oversample, clocking and ppm;
3. computes the Pareto frontier and picks one by `optimization.objectives`;
4. generates the package and records candidates, confidence, parameters and the git commit in `manifest.json`.

Default rules:

- `RATIO <= 128` can use single-cycle `parallel_mux`; larger RATIO uses `word_group_capture`.
- Narrow-input / small-area MPW targets can use endpoint-only `word_streaming`.
- Same-source clocks use `fixed_oversampling`; ppm offset uses `adaptive_oversampling` (OS ≥ 8); a forwarded clock uses `source_sync`.
- `apb_csr`, `async_fifo`, `axis_tx_adapter`, `axis_rx_adapter`, AXIS endpoints, PRBS, pad-level APB BIST and the single/multi-clock SoC wrappers all have verifiable RTL; AXIS packages for `target=tx/rx` and the source-sync `soc` package can be generated directly. `phy_ip` still lacks calibration/DFT integration and requires an explicit `--allow-incomplete`.

## 6. The next abstraction layer

Landed:

- Frame alignment/comma (`comma_align`, one-shot and per-frame realignment).
- Three CDRs: fixed/adaptive/source-sync.
- Word-streaming serializer architecture.
- Spec-driven package generator, three-level SDC, qualification gate.
- 8b/10b and 64b/66b coding-layer packages (`serdes_8b10b_top` / `serdes_64b66b_top`, selected by the generator's `coding:`), scrambler (`pcs_64b66b`), CRC primitives (`crc_frame` / `axis_crc_*`, CRC-16/32 verified). The 8b/10b K-code control path is connected (`tx_charisk/rx_charisk` pass through to `pcs_8b10b_tx.ctrl`, so `/I/ /S/ /T/` are usable); comma alignment now detects 10-bit K28.5 in both disparities and supports `RETRIGGER=1` per-frame realignment; 64b/66b gained a 66-bit sliding-window block lock, control-block type decoding and an RX gearbox. **Boundaries**: the 8b10b package's `enc_ctrl` is still always 0 (K codes must be driven explicitly through `tx_charisk`; the generator does not generate K-code streams); 64b66b control fields are only classified, not byte-extracted, and there is no lane distribution; CRC is not yet wired into the generated package's frame path. The limitations are recorded in `manifest.capabilities.coding.limitations` and `docs/pcs_coding.md`.

Still not implemented / not promised by this directory:

- FEC, a full PCS (K-code ordered-set scheduling, 64b/66b control-field extraction and lane distribution, a multi-lane 64b/66b gearbox), and wiring CRC/reliable transport into the generated package's frame path.
- The single-clock `serdes_soc_wrapper`, multi-clock `serdes_soc_cdc_wrapper`, pad BIST and word/datapath BIST are implemented and tested; APB-controlled injection/loopback/snapshot are integrated. Still missing: calibration, non-source-sync SoC CDR integration, and word BIST in the CDC wrapper (currently only in the single-clock wrapper).
- Scan/DFT, clock gating, BIST/calibration, DFT signoff.
- Verilog-A/RNM wrappers for analog TX/RX, PEX, SI/PI, jitter/BER, silicon correlation.
- Cellmap/floorplan/post-route signoff adapters for target PDKs; `native_dff` is currently still a documentation field, with no automatic `dfflibmap` runner.
- Standard-cell catalog/availability: [`docs/library_catalog.md`]; per-cell DRC/LVS of the sky130 families and routing/PPA of the 9-track mixed library (`hd`+`hdll`): [`docs/sky130_ifamily_mix.md`](docs/sky130_ifamily_mix.md); structural optimization roadmap: [`docs/timing_optimization.md`](docs/timing_optimization.md); sky130 PVT evidence: [`docs/pvt_sky130.md`](docs/pvt_sky130.md); PEX status: [`docs/pex_status.md`](docs/pex_status.md); PDK MC (ngspice local mismatch / Xyce global process): [`docs/mc_pdk.md`](docs/mc_pdk.md); generated-top SoC E2E: [`docs/soc_e2e.md`](docs/soc_e2e.md); analog macro contracts: [`docs/analog_contracts.md`](docs/analog_contracts.md); mixed-signal PEX chain and RX PEX digital recovery: [`docs/mixed_signal.md`](docs/mixed_signal.md); macro PnR status: [`docs/analog_pnr.md`](docs/analog_pnr.md); cross-process macro generator: [`docs/macro_generator.md`](docs/macro_generator.md); review of the external ADPLL (`../adpll_gen`) functional gaps and the `clock_source` contract: [`docs/adpll_gen_review.md`](docs/adpll_gen_review.md); measured comparison of three IHP130 performance paths (OS=2 / drive cells / two-level carry): [`docs/ihp_perf_paths.md`](docs/ihp_perf_paths.md).

## 7. Native DFF mapping strategy

- The RTL only infers sequential elements (`always_ff`) and **never hand-instantiates any PDK DFF cell name**.
- At synthesis, Yosys `dfflibmap -liberty <PDK lib>` maps them to the target PDK's native DFF.
- Confirmed native mappings:

| PDK | Native DFF | Evidence |
|---|---|---|
| sky130A | `sky130_fd_sc_hd__dfrtp_1` | top has 550 of this cell; `unmapped=0` |
| gf180mcuD 7t5v0 | `gf180mcu_fd_sc_mcu7t5v0__dffrnq_1` | top has 550 of this cell; `unmapped=0` |
| IHP SG13G2 | `sg13g2_dfrbpq_1` | 550 of this cell after a manual Yosys `dfflibmap` |
| icsprout55 H7CH (HVT) | `DFFRQX2H7H` | 7T; Top 550 |
| icsprout55 H7CL (LVT) | `DFFRQX2H7L` | 7T; Top 550 |
| icsprout55 H7CR (RVT) | `DFFRQX2H7R` | 7T; Top 550; `unmapped=0` |

OpenSERDES's NAND master-slave DFF / custom DFF is not used as a digital standard cell.
The original `DFF/` and `NAND/` physical cells have moved to `legacy/DFF/` and `legacy/NAND/` as
provenance only; the default digital flow uses the PDK standard-cell DFF/NAND.

All three icsprout55 libraries are **7-track** (cell height 1.4 µm, M1 pitch 0.2 µm):

| Variant | Vt | Use |
|---|---|---|
| `H7CH` | HVT | Low leakage, slowest |
| `H7CL` | LVT | High speed, higher leakage |
| `H7CR` | RVT | Default, balanced |

The three variants have the same cell footprints/areas and differ in Vt, timing and leakage; do not treat them as three different process nodes. GF180's `mcu7t5v0` is also 7T; IHP SG13G2's cell height of 3.78 µm / M1 pitch 0.42 µm is closer to 9T, so cross-PDK area comparisons must normalize for track height.

50 MHz pre-PnR comparison (Top, ss corner):

| Variant | Setup WNS (ss) | Conclusion |
|---|---:|---|
| H7CH (HVT) | −10.65 ns | 50 MHz does not close |
| H7CL (LVT) | +2.38 ns | 50 MHz passes (pre-CTS hold margin only 4.7 ps) |
| H7CR (RVT) | +1.58 ns | 50 MHz passes (pre-CTS hold margin 14.7 ps) |

The Serializer alone at 50 MHz: H7CH −25.97 ns, H7CL −1.01 ns, H7CR −5.63 ns; none of the three closes, so it needs 25 MHz or pipelining of the 256:1 mux. The reasons Top passes include top-level optimization/connectivity differences, so this cannot be used to claim the Serializer passes on its own.

## 8. Generator, presets and qualification

The generator entry point is `scripts/gen_ip.py`; example specs are in `spec/`:

```bash
python3 scripts/gen_ip.py --spec spec/default.yaml --out out/default --force
python3 scripts/gen_ip.py --spec spec/default.yaml --check
python3 scripts/gen_ip.py --list-arch
```

The output package contains `rtl/`, `rtl/filelist.f`, `params.svh/json`, `constraints/{pre_pnr,post_cts,signoff}.sdc`, `pnr/librelane.yaml`, `manifest.json` and `README.md`. Note that `word_group_capture` is still a frame-level hold (`ready` is low for `WORDS` cycles and the external bus must stay stable), not a per-word handshake; choose `word_streaming` when a real per-word handshake is needed. `pre_pnr.sdc` excludes async reset recovery/removal; `post_cts.sdc`/`signoff.sdc` use `set_propagated_clock` and re-enable the reset checks.

| preset | Data | Control | CDC | Status |
|---|---|---|---|---|
| `minimal` | valid/ready parallel | native | none | Available |
| `default` | valid/ready parallel | native | auto | Available |
| `soc` | AXI4-Stream | APB | none or async FIFO | `serdes_soc_wrapper` (single clock) / `serdes_soc_cdc_wrapper` (multi-clock, source-sync link) available |
| `phy_ip` | valid/ready + BIST/calibration/DFT | APB | async FIFO | APB/FIFO primitives available; BIST/DFT integration still a scaffold |

Verification levels are enforced by `scripts/qualify.py`: `RTL_FUNCTIONAL` (lint/sim/synth), `PRE_PNR_TIMING`, `POST_ROUTE_TIMING`, `PHYSICAL_SIGNOFF`, `LINK_VALIDATED`. `scripts/collect_signoff.py` collects PnR/STA/DRC/LVS/CDC/BER reports into `signoff.json`; missing checks are never assumed to pass.

Measured PPA is written to `tech/ppa_measured.yaml` through `scripts/import_ppa.py`; the generator's `--ppa-db` applies it before capability filtering, and the manifest records `ppa_source`, confidence and absolute values. Detailed rules are in [`docs/generator.md`](docs/generator.md) and [`docs/qualification.md`](docs/qualification.md).

## 9. License

The original OpenSERDES is GPL-3.0. This directory is a behavioral/interface-level clean rewrite and does not copy the original gate-level netlists; confirm GPL obligations and target-PDK licenses before distributing externally.
