# OpenSERDES 数字部分：工艺无关抽象层

本目录把 OpenSERDES 的数字部分从 sky130 门级/硬宏中抽离出来，形成可综合、可参数化、无标准单元绑定的 RTL。

## 0. 目录与迁移

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
```

原有 `Serializer/`、`DeSerializer/`、`OverSampling_CDR/` 等目录保留其
GDS/SPICE/LEF/PDF 物理与模拟证据；新数字流程只使用 `rtl/`。完整映射、兼容
adapter 和剩余 gap 见 [`docs/migration.md`](docs/migration.md)。

## 1. 设计原则

- **不出现任何工艺单元名**：没有 `sky130_fd_sc_hd__*`、`sg13g2_*`、`ics55_*`。
- **单时钟**：所有时序逻辑只由 `clk` 驱动，推进由 `enable` 控制。
- **禁止生成时钟**：不再用 `READY`、`COMPLETE` 作为时钟。
- **输入/输出寄存器化**：并行数据只在 `load && ready` 时采样；输出在完成时打拍。
- **参数化数组**：`DATA_WIDTH`、`WORDS`、`LSB_FIRST`；`par_data` 是 `[WORDS-1:0][DATA_WIDTH-1:0]`，位选/字选用 generate 循环生成。
- **复位一致性**：所有叶子块使用异步低有效 `rst_n`；顶层每个时钟域经过 `reset_sync`（异步置位、同步释放）。
- **显式握手**：`ready/load`、`serial_valid`、`par_valid`、`busy`；另有 word-streaming valid/ready 架构。
- **CDR 可插拔**：`fixed_oversampling`、`adaptive_oversampling`、`source_sync` 三种模式，默认由 generator 按 ppm/clocking/rate 选择。
- **帧同步显式化**：`comma_align` 支持单次和 per-frame 重对齐，输出 `rx_frame_locked`、`rx_lock_stale` 和恢复串行流。
- **验证分级**：`RTL_FUNCTIONAL`、`PRE_PNR_TIMING`、`POST_ROUTE_TIMING`、`PHYSICAL_SIGNOFF`、`LINK_VALIDATED`，不同等级的证据不能混用。
- **CDR 是抽象替代实现**：原仓库只有门级 `CLK_RECOVERY`，本目录提供可综合的过采样 CDR；它不是原门级网表的逐周期克隆。

## 2. 模块

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

- `load && ready` 采样 `par_data`。
- 每个 `enable` 输出一个串行位。
- `serial_valid` 在帧内为高；`frame_done` 在最后一位被接受后的下一拍脉冲。
- 位序：
  - `LSB_FIRST=1`：word0 bit0 → word0 bit31 → word1 bit0 ...
  - `LSB_FIRST=0`：word0 bit31 → word0 bit0 → word1 bit31 ...
- `CAPTURE_MODE=1`：用 `WORDS` 拍逐 word 捕获，把 256 个输入寄存器 enable 的
  单网扇出降到 32；代价是 `ready` 低 `WORDS` 拍，外部数据需保持稳定。
  生成器用 `--capture-mode` 选择。实测（one-hot capture）：sky130 10 ns +2.78 ns；
  IHP 10 ns +5.66 ns；H7CL 10 ns +7.21 ns；H7CR 10 ns +6.39 ns；
  H7CH 20 ns +13.15 ns；GF180 5 V 40 ns +22.50 ns。
  跨工艺端点：H7CL TX 5 ns +1.03 ns，H7CL RX 2.5 ns（OS=2）+0.315 ns；
  H7CL top 10 ns TX + OS=2 为 **+2.82 ns（100 MHz 链路）**；
  H7CL top 5 ns TX + OS=2 为 **+0.315 ns（200 MHz 链路）**。
  GF180 5 V：serializer 20 ns +2.50 ns，deserializer 20 ns +8.14 ns；
  但 top 20 ns + OS=2（RX 10 ns）为 −17.13 ns，20 ns 全链路需 source-synchronous RX。

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

- `serial_valid && enable` 接收一个串行位。
- `align` 为高时复位位/字计数器；下一个有效位成为 word0 bit0。它是 comma/training 对齐的挂钩。
- 每个 word 由 `deserializer_word` 的一个 generate 实例持有；`par_data` 直接来自这些 word 寄存器，**没有 `INT_PAR_IN` 影子寄存器，也没有第二级 `PAR_IN` 锁存**。
- 帧完成后 `par_data` 已经是 word/bit 顺序，`par_valid` 脉冲一拍。
- word/bit 选择使用 one-hot 移位寄存器，捕获路径没有二进制比较器；H7CL 2.5 ns RX +0.315 ns。
- 无 `COMPLETE` 生成时钟。

### `rtl/deserializer_word.sv`

每个 word 一个 `DATA_WIDTH` 寄存器组；`capture` 只在选中的 word 且 `serial_valid` 时有效。`par_data` 直接映射到这些 word 寄存器，修复了原 DeSer 的 `INT_PAR_IN` 传递问题。

### `rtl/serdes_soc_wrapper.sv`

单时钟 SoC 参考顶层：APB CSR + TX AXIS + source-sync RX AXIS。APB map：reg0 control、reg1..6 scratch/保留、reg7 status、reg8 BIST_CTRL、reg10/11 pad BIST 计数、reg12 BIST_STATUS、reg13/14 word BIST 错误/帧计数。TX AXIS frame 为 comma + `WORDS` payload words，RX AXIS frame 为 `WORDS` payload words；`ALIGN_MODE=2` 每帧 comma 重对齐。`BIST_CTRL[0]` + `[6]=0` 是 pad 级模式：PRBS 直接驱动 `tx_serial_out` 并旁路 core；`BIST_CTRL[6]=1` 是 word/数据通路模式：PRBS 帧源替代 AXIS 输入，RX AXIS 输出被 checker 消费，完整经过 adapter/serializer/RX core。`BIST_CTRL[4]` 选择近端环回。pad 模式内部拉低 TX/RX core enable；计数器经 `bist_snapshot` 的请求/应答握手读取，避免撕裂。该 wrapper 用于单时钟集成验收；`spec/soc_single_clock.yaml` 是示例。

### `rtl/serdes_soc_cdc_wrapper.sv`

多时钟 SoC 参考顶层：`pclk`/`s_axis_aclk`/`m_axis_aclk`/`tx_clk`/`rx_clk` 分离；TX/RX AXIS 各接 `async_fifo`，APB control/status 经 `cdc_sync` 2FF 同步。链路仍是 source-sync（`OVERSAMPLE=1`、comma per-frame），AXIS/APB 时钟可以与 link clock 不同。BIST 的 PRBS generator/checker 放在 `tx_clk` 域（连续比特流不能跨时钟），控制位逐位 `cdc_sync`，计数通过 `bist_snapshot` 稳定总线握手回到 `pclk`；非环回的外部 `rx_serial_in` 检查要求 `tx_clk`/`rx_clk` 同频同相，环回模式是确定性自检模式。`spec/soc.yaml` 生成该 wrapper。

### `rtl/serdes_bist.sv`

PRBS BIST wrapper：`prbs_gen` + `prbs_check` 共享 `start/clear` 和 seed；输出 `serial_out`，检查 `serial_in`，暴露 `error/error_count/bit_count`。两个 SoC wrapper 已通过 APB 集成该原语：`BIST_CTRL` 控制 enable/start/clear/inject/loopback/snapshot，`BIST_ERROR_COUNT`/`BIST_BIT_COUNT` 为快照只读寄存器，`BIST_STATUS` 汇总 error/running/valid/width/word_mode。它是 pad 级比特流 BIST，旁路 serializer/deserializer；数据通路级验证由下面的 word BIST 提供。

### `rtl/prbs_word_gen.sv` / `rtl/prbs_word_check.sv`

word/数据通路级 PRBS：generator 每帧发出 comma + `WORDS` 个 PRBS word（每 word 为 `DATA_WIDTH` 个连续 PRBS 位，comma 后重新从 seed 开始），checker 按 AXIS beat 逐 word 比对并在 `tlast` 结帧。两者组成“AXIS 级 BIST 帧源/信宿”，在单时钟 SoC wrapper 中通过 `BIST_CTRL[6]` 启用，覆盖 `axis_*_adapter`、serializer、RX core 和 comma 对齐；错误计数为比特错误数，另有帧计数。

### `rtl/prbs_gen.sv` / `rtl/prbs_check.sv`

PRBS7/15/31 串行 generator/checker，带 sticky `error`、`error_count` 和 `bit_count`。作为 BIST/链路测试原语；`soc`/`phy_ip` package 会把它连同 BIST wrapper 一起打包（soc wrapper 已直接依赖）。

### `rtl/axis_tx_adapter.sv` / `rtl/axis_rx_adapter.sv`

### `rtl/serializer_half_rate.sv`

Half-rate TX 原型：`clk_word` 负责捕获/握手，`clk_bit = 2*clk_word` 逐 bit 输出；
request/acknowledge 跨两个时钟域。与 `serializer_stream` 位精确对照（8x4/16x4/
32x8/5x3、LSB/MSB、40 帧）。sky130 HD TT 5 ns bit clock 下 worst slack
**0.658 ns**（baseline 0.406 ns，约 +0.25 ns）。仍是原型，未接入 SoC/generator，
`enable` 和完整 RX half-rate 状态机待补。

### `rtl/receiver_pair_sampler.sv`

Full-rate sampler boundary for the half-rate RX prototype：bit clock 采样 2-bit
history，在 word clock 输出 `bit0`（较早）/`bit1`（较新）与 `pair_valid`；
`tb_receiver_pair_sampler` 与参考 history 对照。它不包含 CDR、comma alignment
或完整帧 valid 语义。

### `rtl/deserializer_half_rate.sv`

Half-rate RX 原型的并行 bank：每个 `clk_word` 接收 2 bit（bit0 先、bit1 后），
word/bit 计数和 `par_valid` 在 word clock 运行；DDR/2-phase sampler、CDR 和
comma alignment 不在该原型内。`tb_deserializer_half_rate` 覆盖 8x4/16x4/32x8、
LSB/MSB、50 帧。sky130 HD TT 10 ns word clock 下 worst slack 6.866 ns
（full-rate deserializer 5 ns 为 2.843 ns；周期不同，不能当作端到端 RX 结论）。

### `rtl/serializer_hier_2to1.sv`

`serializer_stream` 的位精确 2:1 分级 word 选择树原型：接口/握手/输出时序相同，
用 balanced 2:1 mux 代替 flat WORDS-way one-hot mux。已通过 8x4/32x8/5x3、
LSB/MSB、随机 backpressure 对照 TB；sky130 HD 5 ns 的首次 Yosys/ABC 对比
worst slack 与 baseline 相同（0.406 ns），说明纯组合 mux tree 本身不会被 ABC
保留为更短路径；下一步需要寄存器化树级/half-rate 才能真正改变 critical path。
见 [`docs/timing_optimization.md`](docs/timing_optimization.md)。

AXIS word adapter：一个 beat 是一个 `DATA_WIDTH` word，`tlast` 标最后一 word。TX adapter 驱动 `serializer_stream` 的 `in_valid/in_ready/in_word`；RX adapter 把 `rx_frame_valid` 的并行 frame 拆成 AXIS beats，并在上一帧未排空时置 `frame_overflow`。

`serdes_tx_axis_digital` / `serdes_rx_axis_digital` 是 AXIS endpoint wrapper；AXIS 时钟与 `tx_clk`/`rx_clk` 相同，跨时钟域需要外接 AXIS async FIFO。

### `rtl/apb_csr.sv` / `rtl/async_fifo.sv`

`apb_csr` 是 word-aligned APB 控制/状态寄存器组：默认最后一个地址只读 `status_i`；`STATUS_IDX` 可移动状态寄存器，`RO_MASK`/`ro_i` 可增加任意只读寄存器（BIST 计数即用此机制），只读地址忽略写入；非 word 对齐或越界访问返回 `pslverr`。它用于 `soc`/`phy_ip` preset 的配置接口，目前是独立原语，尚未强制接入数字 top。

`async_fifo` 是双时钟 Gray-pointer FIFO，`DEPTH` 必须为 2 的幂；小深度用寄存器阵列实现。它可作为 RX 恢复时钟域到本地 SoC 时钟域的 CDC 原语，但 CDC clean 仍需目标工具的 CDC 检查，不能由 RTL 自证。

### `rtl/link_training_fsm.sv` / `rtl/crc_frame.sv`

`link_training_fsm` 是独立的数字训练状态机：先扫描相位轴，再扫描阈值轴，用单调错误计数在每个候选的 dwell 窗口内做差，冻结最佳设置；锁定时持续监测错误增长，超阈值会自动重新训练。输出 `phase_sel`/`thresh_sel` 只是**执行器请求**，`phase_we`/`thresh_we` 是单周期装载脉冲。当前 CDR 没有外部相位/阈值输入，因此该 FSM **尚未接入** `serdes_digital_top`；不要把它当成已验证的 link training。

`crc_frame` 是参数化串行 CRC 原语，已验证 CRC-16/CCITT-FALSE（`0x29B1`）和 CRC-32/ISO-HDLC（`0xCBF43926`）。它只做 CRC 累加，不定义 AXIS 帧、sync word、sequence ID 或重传，尚未接入生成包的 frame 路径。

训练/CRC 的能力边界和后续接入条件见 `docs/digital_training_crc.md`。

### `rtl/axis_crc_inserter.sv` / `rtl/axis_crc_checker.sv`

AXIS CRC 插入/检查原语。插入器把 CRC beats 追加在 payload 之后，用 `m_axis_tuser` 标 payload 最后一拍，`m_axis_tlast` 留给最后 CRC beat；检查器转发 payload、消费 CRC beats，并输出 `crc_ok` / `crc_error`。`serdes_tx_axis_digital` / `serdes_rx_axis_digital` 通过 `CRC_ENABLE` 可选启用该路径，serialized frame 增加 `ceil(CRC_WIDTH/DATA_WIDTH)` 个 word；SoC async wrapper 尚未暴露 CRC CSR。

### `rtl/encoder_8b10b.sv` / `rtl/decoder_8b10b.sv`

8b/10b 编解码器，已验证全部 256 个数据字节和 K28.5 round-trip。实现改编自 MIT 许可的 `libsv`，来源和许可证见 `docs/third_party.md`。`pcs_8b10b_tx/rx` 提供带 valid/error 的 PCS wrapper；`serdes_8b10b_top` 把 8-bit payload 编码到 10-bit line word，generator 在 `payload_width: 8`、`target: link`、`align: external` 时接受 `coding: 8b10b`。

### `rtl/pcs_64b66b.sv`

64b/66b 原语：2-bit sync header + 64-bit self-synchronous scrambler payload，支持 data/control block header、非法 sync 拒绝，以及 66-to-64-bit gearbox。`serdes_64b66b_top` 把 64-bit payload 编码到 66-bit line word；generator 在 `payload_width: 64`、`target: link`、`align: external` 时接受 `coding: 64b66b`。完整 IEEE control-field、lane distribution 仍是独立 PCS 工作。

### `rtl/receiver_sampler.sv`

Receiver 数字边界的 1-bit 采样模型：只建模原 `Receiver.sp` 中 2 GHz NAND DFF 的 `q <= d` 行为，不建模 resistive-feedback inverter、CMOS inverter 或模拟 setup/hold。

### `rtl/reset_sync.sv`

异步置位、同步释放的复位同步器。顶层 `serdes_digital_top` 为 TX/RX 两个时钟域各实例化一个。

### `rtl/serdes_tx_digital.sv` / `rtl/serdes_rx_digital.sv`

TX/RX 独立端点 wrapper。两者可以分别用不同 PDK 综合，再在同一链路中组合；协议/RTL 不变，只换目标库和 analog wrapper。

`serdes_rx_digital` 的 `CDR_MODE` 选择 CDR：`0=fixed_oversampling`、`1=adaptive_oversampling`、`2=source_sync`；旧的 `CDR_BYPASS=1` 等价于 `CDR_MODE=2`。source-sync 模式下外部 `rx_clk` 作为 bit clock，经 `receiver_sampler` 后直接进入 deserializer；此时 `rx_clk_out=rx_clk`、`rx_lock=1`，`OVERSAMPLE` 不参与。

`ALIGN_MODE`：`0=外部 rx_align`、`1=comma 单次对齐`、`2=comma 每帧重对齐`。新增输出 `rx_frame_locked`、`rx_frame_valid`（= `rx_par_valid & rx_frame_locked`）、`rx_serial_data`、`rx_serial_valid`、`rx_freq_trim`，用于链路 scoreboard、状态恢复和 calibration 观测。

### `rtl/serdes_digital_top.sv`

由 `serdes_tx_digital` + `serdes_rx_digital` 组成的数字顶层。TX/RX 时钟独立；`rx_clk_out` 仅监视，`rx_sample_strobe` 才是数据有效选通。`rx_frame_locked` 表示 comma/training 帧对齐已建立；`rx_lock_stale` 只表示长时间没有边沿，不应单独用来停止接收。`TRAINING_ENABLE=1` 时，`train_error_count` 驱动 `link_training_fsm`，其 `phase_sel` 接到 CDR 的 `phase_offset`。`serdes_soc_cdc_wrapper` 还提供 rx_clk 域训练和 APB CSR 13/14 telemetry（best_errors、locked/busy/retrain/phase）；错误源仍由外部 scoreboard/PRBS checker 提供，generator 开关尚未接入，默认关闭。

### `rtl/cdr_sync.sv` / `rtl/cdr_oversampling.sv` / `rtl/cdr_adaptive.sv`

CDR 被拆成明确的三段：

```text
raw data -> cdr_sync (STAGES flops) -> edge detect + data select
         -> phase counter -> sample_strobe -> deserializer
```

`cdr_sync` 的 `samples[0]` 是最新样本，`samples[STAGES-1]` 是最老、已稳定的样本。边沿检测和**数据选择都使用老样本**，因此增加 `SYNC_STAGES` 会真正增加数据路径的亚稳态建立时间，而不只是边沿检测路径。默认 `SYNC_STAGES=3`，MTBF 仍需在目标 PDK 上确认同步寄存器未被优化/合并。

```
cdr_oversampling #(
  OVERSAMPLE = 4, SYNC_STAGES = 3,
  LOCK_THRESH = 8, LOCK_STALE_BITS = 16
) (
  clk, rst_n, enable, flush, data_in,
  data_out, sample_strobe, clk_out, lock, lock_stale
);
```

- `fixed_oversampling`：捕获阶段每个边沿把相位计数器装 0；锁定后自由运行。它不跟踪频偏。
- `sample_strobe` 每比特脉冲一次，是**数据有效选通**；应使用它，而不是 `clk_out`，来接收 `data_out`。
- `lock` 在连续检测到 `LOCK_THRESH` 个边沿后置位；长时间没有边沿只置 `lock_stale`，**不会**用数据模式相关的衰减把 lock 拉低。
- `flush` 清除 lock/相位状态，用于协议恢复。
- 约束：`OVERSAMPLE ≥ 4`；`OVERSAMPLE=2` 是受限制模式，generator 必须显式 `allow_os2: true`。

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

- `adaptive_oversampling`：用 fractional period control 偶尔插入/删除一个采样周期，跟踪 TX/RX ppm 频偏；`freq_trim` 是带符号环路状态。
- 锁定后有一个 `LOOP_SETTLE_BITS` 窗口，避免捕获瞬态把采样相位推偏；deadband 过滤 1/OVERSAMPLE UI 的量化抖动。
- 这是**一阶频率跟踪环**，不是二阶 jitter-filtering PLL。建议 `OVERSAMPLE ≥ 8`；高抖动链路应使用 phase interpolator/PLL CDR。
- 已验证 `PPM = 0, +700, -700` 和 `100 ppm`，5000 bits 连续恢复无 bit slip（`tb_cdr_adaptive`，`FRAC_BITS=12`）。**限制**：jitter tolerance、gate-level equivalence 和模拟前端仍未验证；生产级高抖动链路仍应使用二阶环或 phase interpolator。

### `rtl/comma_align.sv`

在恢复串行流中搜索 `SYNC_PATTERN`（默认 8'hBC）。匹配发生在 pattern 最后一个 bit；该 bit 被 deserializer 的 `align` 优先级丢弃，下一个有效 bit 成为 word0 bit0。`ALIGN_MODE=2` 时每个 `rx_par_valid` 后重新 arm，支持连续/带空隙的 per-frame 重对齐。`flush` 或 CDR lock 丢失会清除 `rx_frame_locked`。

## 3. 与 OpenSERDES 原实现的关键差异

| 原实现 | 抽象层 |
|---|---|
| Serializer 依赖 `READY` 边沿、`int_PAR` 多驱动且被综合删除 | 单 `clk` + `load/ready`，输入寄存器真正保留 |
| Deserializer 256 个 DFF 由 `COMPLETE` 直接打拍 | 单 `clk` + `enable`，无生成时钟 |
| Deserializer `4'd3` 分支缺少 `OUT[0] <= SERIAL_IN` | 按位索引写入，无 state-3 位滑移 |
| 300 个并行输出直接暴露 | `WORDS×DATA_WIDTH` 参数化数组，可由 generator 选择并行/串行配置 |
| DeSer `INT_PAR_IN` 没有正确传递 | 每个 word 单寄存器组，`par_data` 直连，无影子锁存 |
| CDR 只有门级网表、22 位 SCAN_IN 语义未文档化 | fixed/adaptive/source-sync 三种可综合模式；明确 `sample_strobe/lock/lock_stale/freq_trim` 契约；`comma_align` 帧同步 |
| 原 Serializer 只有 13 个 DFF（输入寄存器被删），DeSer 更多 | 抽象后 Serializer 266 FF、DeSer 266 FF、Top 550 FF；单时钟 + `enable`，必须走 CTS；见 `docs/clocking.md` |
| 无 testbench | 32 个自检 TB（serializer LSB/MSB/capture/stream、deserializer LSB/MSB、loopback LSB/MSB、32×8、link scoreboard、channel、fixed/adaptive CDR、frame sync、CDR bypass、compat legacy/port-order、APB CSR、async FIFO、AXIS TX/RX adapter + endpoint、PRBS7/15/31、pad BIST7/15/31、word BIST、单/多时钟 SoC wrapper） |

## 4. 验证

```bash
make sim      # iverilog：32 个自检 TB
make regress  # 参数矩阵 + link/scoreboard + generator 包检查
make lint     # Verilator -Wall（0 warning）
make synth    # Yosys 通用门级检查（先经 sv2v v0.0.12 转成 Verilog）
make gen SPEC=spec/default.yaml OUT=out/default   # 包内生成 rtl/sv2v/*.v 供 LibreLane/Yosys
make qualify REPORT=signoff.json LEVEL=PRE_PNR_TIMING
make signoff PACKAGE=out/default REPORTS=out/default/reports SIGNOFF_LEVEL=POST_ROUTE_TIMING
make verify-link VERIFY_PACKAGE=out/soc  # bound SI/BER + RTL JTOL gate -> reports/ber.json
make ppa-import PPA_CSV=measured.csv PPA_OUT=tech/ppa_measured.yaml
```

已验证：

- `tb_serializer` LSB/MSB/capture：PASS
- `tb_serializer_stream`：PASS（per-word valid/ready，背靠背帧）
- `tb_deserializer` LSB/MSB：PASS
- `tb_serdes_loopback` LSB/MSB：PASS
- `tb_serdes_default` 32×8：PASS
- `tb_serdes_channel`：真实 `rx_serial_data` 串行 scoreboard + `rx_par_data` 并行帧 scoreboard；覆盖 comma per-frame 对齐、帧间空隙、`rx_flush` 后重锁：PASS
- `tb_channel` delay/invert/error 注入：PASS
- `tb_cdr`：固定 CDR，PRBS7，OS=4 和 OS=2 各 200 个恢复 bit 与发送序列逐 bit 比较：PASS
- `tb_cdr_adaptive`：0 / +700 / -700 / 100 ppm，5000 bit 连续恢复：PASS
- `tb_frame_sync`：comma、gap、mid-frame flush/re-acquire：PASS
- `tb_cdr_bypass`：source-sync receiver-only：PASS
- `tb_compat_legacy` / `tb_compat_port_order`：named 和 positional legacy 兼容：PASS
- Verilator lint：0 warning
- Yosys synth：仅通用门/DFF，无 latch、无 memory
- `scripts/regress.py`：63/63，含参数矩阵、link scoreboard、APB/FIFO、AXIS endpoint、PRBS/BIST、单/多时钟 SoC wrapper、generator package、signoff collector 和 measured-PPA gate

SI/BER 验证框架（`verification/`，纯 Python + 可选 iverilog）：

```bash
pip install -r verification/requirements.txt
python -m verification.cli eye     --profile verification/profiles/stressed.yaml --out build/verify/eye.png
python -m verification.cli bathtub --profile verification/profiles/stressed.yaml --out build/verify/bathtub.png
python -m verification.cli eye --profile verification/profiles/stressed.yaml --channel-file channel.s4p --channel-port-map tx_p=0,tx_n=1,rx_p=2,rx_n=3 --out build/verify/eye_touch.png
python -m verification.cli jtol    --profile verification/profiles/stressed.yaml --out build/verify/jtol.json --model analytic
python -m verification.rtl.runner  --nbits 800 --sj-peak-ui 0.2 --sj-freq-hz 500000   # 真实 cdr_adaptive
python -m verification.rtl.runner  --nbits 600 --chain rx --noise-rms-v 0.12      # 完整 serdes_rx_digital + 模拟信道切片
make verify-link VERIFY_PACKAGE=out/soc PYTHON=/usr/bin/python3                            # capability gate -> reports/ber.json
make verify-link VERIFY_PACKAGE=out/soc VERIFY_ANALOG_LUT=build/verify/sky130_lut.json  # 绑定 package + sky130 LUT
make analog-lut ANALOG_TECH=sky130 PYTHON=/usr/bin/python3                                  # tech config 驱动，自动发现 ~/.ciel/ciel 的 sky130 PM3 模型
make analog-tech-check ANALOG_TECH=sky130 PYTHON=/usr/bin/python3                           # 打印模型/Magic/KLayout 解析结果
make sky130-pvt PYTHON=/usr/bin/python3                                                    # 45 点 corner x V x T LUT
make sky130-mc PYTHON=/usr/bin/python3 MC_SAMPLES=20                                       # behavioral mismatch MC（非 PDK MC）
make pex-compare SCHEM_LUT=... PEX_LUT=...                                                # PEX vs schematic LUT delta
make analog-lut-demo PYTHON=/usr/bin/python3                                                # 无 PDK 时的 generic 自检（非 sky130）
```

`make verify-link VERIFY_PACKAGE=<out>` 读取 package manifest 的 line rate、DATA_WIDTH、WORDS、SYNC_LEN、design/interface，拒绝未绑定或参数不一致的声明；通过后把 eye/bathtub/JTOL 结果写成 `reports/ber.json` 并回写 `link_capability`。`--chain frame`（默认）用实际生成的帧宽度做 `rx_par_data` scoreboard。`verification/analog/` 提供 tech-config 驱动的原理图级 LUT 提取（ngspice + PDK 模型，不含 PEX；sky130 已支持，gf180/ihp 为 scaffold）；`scripts/verify_link.py --analog-lut ... --tx-load-ff ...` 可用 TX rise time 推导的 RC tau 替换 profile 值。PVT/PEX 仍不在声明范围内。

`verification/` 把三类问题分开：2-UI eye / eye metrics、固定相位或固定阈值扫描的经验 BER bathtub（0 误码只报 3/N 上界，不报 BER=0）、以及 Gaussian 尾部统计 BER。`jtol --model analytic` 是明确标注的固定相位演示，不含 CDR；`verification.rtl.runner` 用 iverilog 驱动真实 RTL：`--chain cdr` 直接驱动 `cdr_adaptive`，`--chain rx` 把 Python 模拟信道（ISI + AWGN + 阈值）切片后的码流送入完整 `serdes_rx_digital` 链；`--chain frame` 进一步加 comma 帧格式，用 `rx_par_data`/`rx_frame_valid` 做帧级 scoreboard。统计实际恢复比特/帧的 BER、bit slip 与 lock loss。它仍不是模拟 TX/RX、PEX 或晶体管级模型；详见 `verification/README.md`。

CI（`.github/workflows/ci.yml`）在每次 push/PR 上安装 pinned sv2v、生成 `out/ci-soc`、运行 `make sim lint synth regress`、框架测试和绑定 package 的 `make verify-link`，并上传 `ber.json`；缺 PyYAML、iverilog、sv2v 或任一 gate 失败都会使 CI 失败。原生 SystemVerilog 仍用于 iverilog/Verilator，Yosys/LibreLane 只读 `rtl/sv2v/*.v`。

顶层/信道/SDC：

- `rtl/serdes_digital_top.sv`：数字顶层
- `tb/channel_model.sv`：延迟/反相/确定性误码注入的数字信道模型（不含模拟损耗/ISI）
- `constraints/serdes_digital.sdc`：TX/RX 双时钟、异步 clock group、uncertainty、I/O delay 模板（模板值未用目标 PDK 工具签核，生成器需替换周期/uncertainty）
- `scripts/gen_ip.py`：spec → 自包含 package（RTL/filelist/params/3 级 SDC/PnR config/manifest）
- `scripts/qualify.py`：按 `RTL_FUNCTIONAL → ... → LINK_VALIDATED` 检查证据，拒绝跨级声明
- `docs/clocking.md`：原网表时钟树审计与 skew 预算（已按 3 级 CTS/均衡扇出修正）
- `docs/prepnr_sta.md`：pre-PnR STA 方法学、reset/control 分离、三种工艺分级与线延迟预算
- `tech/<pdk>/tech.yaml`：PDK/SCL/LIB/corner/CTS 目标；路径用 `${PDK_ROOT}` / `${PDK_LIB_ROOT}` 展开，见 `tech/README.md`
- `scripts/analyze_clock_tree.py`：从 `.lvs.v` 提取时钟树深度/扇出

## 5. 参数与目标工艺

`DATA_WIDTH`/`WORDS` 不绑定工艺；`RATIO=DATA_WIDTH*WORDS` 在模块内部派生，避免参数不一致。目标工艺由 `tech/<pdk>/tech.yaml` 的 `limits` 选择，实际字段为：

```yaml
limits:
  class: MS
  default_period_ns: 40.0
  serial_mbps: 25
  max_endpoint_line_rate_mbps: 100
  max_link_line_rate_mbps: 50
```

这些 gate 来自 pre-PnR 测量，是保守值，不是 signoff。generator 会按 `target=link/tx/rx` 选择 link 或 endpoint gate，并拒绝超限配置；`--relax-limits` 只用于显式实验。

生成器不再按“工艺名 → 固定架构”查表，而是：

1. 从 `tech/arch_db.yaml` 读取候选架构和相对 area/power/latency；
2. 按 target、RATIO、line rate、oversample、clocking、ppm 过滤；
3. 计算 Pareto frontier，并按 `optimization.objectives` 选一个；
4. 生成 package，并在 `manifest.json` 记录候选、confidence、参数和 git commit。

默认规则：

- `RATIO <= 128` 可用 single-cycle `parallel_mux`；更大 RATIO 用 `word_group_capture`。
- 窄输入 / MPW 小面积目标可用 endpoint-only `word_streaming`。
- 同源时钟用 `fixed_oversampling`；有 ppm 频偏用 `adaptive_oversampling`（OS ≥ 8）；有 forwarded clock 用 `source_sync`。
- `apb_csr`、`async_fifo`、`axis_tx_adapter`、`axis_rx_adapter`、AXIS endpoint、PRBS、pad 级 APB BIST 和单/多时钟 SoC wrapper 均有可验证 RTL；`target=tx/rx` 的 AXIS package 和 source-sync `soc` package 可以直接生成。`phy_ip` 仍缺 calibration/DFT 集成，必须显式 `--allow-incomplete`。

## 6. 下一层抽象

已落地：

- Frame alignment/comma（`comma_align`，单次和 per-frame 重对齐）。
- fixed/adaptive/source-sync 三种 CDR。
- word-streaming serializer 架构。
- spec-driven package generator、三级 SDC、qualification gate。

仍未实现 / 不在本目录承诺：

- 8b10b/64b66b、scrambler、CRC、FEC。
- 单时钟 `serdes_soc_wrapper`、多时钟 `serdes_soc_cdc_wrapper`、pad BIST 和 word/数据通路 BIST 已实现并测试；APB 控制的注入/环回/快照已集成。仍缺 calibration、non-source-sync SoC CDR 集成，以及 CDC wrapper 的 word BIST（当前只在单时钟 wrapper 中）。
- Scan/DFT、clock gating、BIST/calibration、DFT signoff。
- 模拟 TX/RX 的 Verilog-A/RNM wrapper、PEX、SI/PI、jitter/BER、silicon correlation。
- 目标 PDK 的 cellmap/floorplan/post-route signoff adapter；当前 `native_dff` 仍是文档字段，尚无自动 `dfflibmap` runner。
- 标准单元目录/可用性见 [`docs/library_catalog.md`](docs/library_catalog.md)，结构优化路线见 [`docs/timing_optimization.md`](docs/timing_optimization.md)，sky130 PVT 证据见 [`docs/pvt_sky130.md`](docs/pvt_sky130.md)，PEX 状态见 [`docs/pex_status.md`](docs/pex_status.md)，PDK MC（ngspice 局部失配 / Xyce 全局 process）见 [`docs/mc_pdk.md`](docs/mc_pdk.md)，generated-top SoC E2E 见 [`docs/soc_e2e.md`](docs/soc_e2e.md)，模拟宏契约见 [`docs/analog_contracts.md`](docs/analog_contracts.md)，混合信号 PEX 链与 RX PEX 数字恢复见 [`docs/mixed_signal.md`](docs/mixed_signal.md)，宏 PnR 状态见 [`docs/analog_pnr.md`](docs/analog_pnr.md)，跨工艺宏生成器见 [`docs/macro_generator.md`](docs/macro_generator.md)。

## 7. 原生 DFF 映射策略

- RTL 只推断时序元件（`always_ff`），**不手写任何 PDK DFF 单元名**。
- 综合阶段用 Yosys `dfflibmap -liberty <PDK lib>` 映射到目标 PDK 的原生 DFF。
- 已确认的原生映射：

| PDK | 原生 DFF | 证据 |
|---|---|---|
| sky130A | `sky130_fd_sc_hd__dfrtp_1` | top 550 个该单元；`unmapped=0` |
| gf180mcuD 7t5v0 | `gf180mcu_fd_sc_mcu7t5v0__dffrnq_1` | top 550 个该单元；`unmapped=0` |
| IHP SG13G2 | `sg13g2_dfrbpq_1` | 手动 Yosys `dfflibmap` 后 550 个该单元 |
| icsprout55 H7CH (HVT) | `DFFRQX2H7H` | 7T；Top 550 个 |
| icsprout55 H7CL (LVT) | `DFFRQX2H7L` | 7T；Top 550 个 |
| icsprout55 H7CR (RVT) | `DFFRQX2H7R` | 7T；Top 550 个；`unmapped=0` |

不使用 OpenSERDES 的 NAND 主从 DFF / 自定义 DFF 作为数字标准单元。
原始 `DFF/` 和 `NAND/` 物理单元已移入 `legacy/DFF/`、`legacy/NAND/`，只作
provenance；默认数字流程使用 PDK 标准单元 DFF/NAND。

icsprout55 的三种库都是 **7-track**（cell height 1.4 µm，M1 pitch 0.2 µm）：

| 变体 | Vt | 用途 |
|---|---|---|
| `H7CH` | HVT | 低漏电，速度最慢 |
| `H7CL` | LVT | 高速，漏电较高 |
| `H7CR` | RVT | 默认平衡 |

三种变体的 cell footprint/面积相同，差别在 Vt、时序和漏电；不要把它们当成三个不同工艺节点。GF180 的 `mcu7t5v0` 也是 7T；IHP SG13G2 的 cell height 3.78 µm / M1 pitch 0.42 µm 更接近 9T，跨 PDK 面积对比必须归一化 track 高度。

50 MHz pre-PNR 对比（Top，ss corner）：

| 变体 | Setup WNS (ss) | 结论 |
|---|---:|---|
| H7CH (HVT) | −10.65 ns | 50 MHz 不闭合 |
| H7CL (LVT) | +2.38 ns | 50 MHz 通过（pre-CTS hold 余量仅 4.7 ps） |
| H7CR (RVT) | +1.58 ns | 50 MHz 通过（pre-CTS hold 余量 14.7 ps） |

Serializer 单独在 50 MHz：H7CH −25.97 ns、H7CL −1.01 ns、H7CR −5.63 ns，三者都不闭合；需要 25 MHz 或对 256:1 mux 加流水。Top 通过的原因包含顶层优化/连接差异，不能据此宣称 Serializer 单独也通过。

## 8. Generator、presets 与 qualification

生成器入口是 `scripts/gen_ip.py`，spec 示例在 `spec/`：

```bash
python3 scripts/gen_ip.py --spec spec/default.yaml --out out/default --force
python3 scripts/gen_ip.py --spec spec/default.yaml --check
python3 scripts/gen_ip.py --list-arch
```

输出 package 包含 `rtl/`、`rtl/filelist.f`、`params.svh/json`、`constraints/{pre_pnr,post_cts,signoff}.sdc`、`pnr/librelane.yaml`、`manifest.json` 和 `README.md`。注意 `word_group_capture` 仍是 frame-level hold（`ready` 低 `WORDS` 拍，外部总线需保持稳定），不是 per-word handshake；需要真正的逐 word 握手时选 `word_streaming`。`pre_pnr.sdc` 排除 async reset recovery/removal；`post_cts.sdc`/`signoff.sdc` 用 `set_propagated_clock` 并重新启用 reset 检查。

| preset | 数据 | 控制 | CDC | 状态 |
|---|---|---|---|---|
| `minimal` | valid/ready parallel | native | none | 可用 |
| `default` | valid/ready parallel | native | auto | 可用 |
| `soc` | AXI4-Stream | APB | none 或 async FIFO | `serdes_soc_wrapper`（单时钟）/ `serdes_soc_cdc_wrapper`（多时钟，source-sync link）可用 |
| `phy_ip` | valid/ready + BIST/calibration/DFT | APB | async FIFO | APB/FIFO 原语可用；BIST/DFT 集成仍 scaffold |

验证等级由 `scripts/qualify.py` 强制执行：`RTL_FUNCTIONAL`（lint/sim/synth）、`PRE_PNR_TIMING`、`POST_ROUTE_TIMING`、`PHYSICAL_SIGNOFF`、`LINK_VALIDATED`。`scripts/collect_signoff.py` 把 PnR/STA/DRC/LVS/CDC/BER 报告收集成 `signoff.json`；缺失的检查不会被猜测为通过。

实测 PPA 通过 `scripts/import_ppa.py` 写入 `tech/ppa_measured.yaml`，generator 的 `--ppa-db` 会在能力过滤前应用；manifest 记录 `ppa_source`、confidence 和绝对值。详细规则见 [`docs/generator.md`](docs/generator.md) 和 [`docs/qualification.md`](docs/qualification.md)。

## 9. 许可

原 OpenSERDES 为 GPL-3.0。本目录是行为/接口级的 clean rewrite，未复制原门级网表；对外分发前请确认 GPL 义务与目标 PDK 许可。
