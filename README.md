# OpenSERDES 数字部分：工艺无关抽象层

本目录把 OpenSERDES 的数字部分从 sky130 门级/硬宏中抽离出来，形成可综合、可参数化、无标准单元绑定的 RTL。

## 0. 目录与迁移

```text
rtl/            canonical digital RTL
rtl/compat/     legacy module-name adapters
tb/             self-checking simulations
constraints/    SDC templates
scripts/        technology/config/flow generators
tech/           per-PDK profiles and PDK_ROOT/PDK_LIB_ROOT setup
               (see tech/README.md)
docs/           migration, clocking, pre-PnR STA, CDR/RX study
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
- **显式握手**：`ready/load`、`serial_valid`、`par_valid`、`busy`。
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

### `rtl/receiver_sampler.sv`

Receiver 数字边界的 1-bit 采样模型：只建模原 `Receiver.sp` 中 2 GHz NAND DFF 的 `q <= d` 行为，不建模 resistive-feedback inverter、CMOS inverter 或模拟 setup/hold。

### `rtl/reset_sync.sv`

异步置位、同步释放的复位同步器。顶层 `serdes_digital_top` 为 TX/RX 两个时钟域各实例化一个。

### `rtl/serdes_tx_digital.sv` / `rtl/serdes_rx_digital.sv`

TX/RX 独立端点 wrapper。两者可以分别用不同 PDK 综合，再在同一链路中组合；协议/RTL 不变，只换目标库和 analog wrapper。

`serdes_rx_digital` 的 `CDR_BYPASS=1` 进入 receiver-only 模式：外部 `rx_clk` 作为 bit clock，经 `receiver_sampler` 后直接进入 deserializer；此时 `rx_clk_out=rx_clk`、`rx_lock=1`，`OVERSAMPLE` 不参与。生成器对应 `--cdr-bypass`。

### `rtl/serdes_digital_top.sv`

由 `serdes_tx_digital` + `serdes_rx_digital` 组成的数字顶层。TX/RX 时钟独立；`rx_clk_out` 仅监视，`rx_sample_strobe` 才是数据有效选通。`rx_align` 用于未来 comma/training 对齐。

### `rtl/cdr_oversampling.sv`

```
cdr_oversampling #(
  OVERSAMPLE   = 4,
  SYNC_STAGES  = 2,
  LOCK_THRESH  = 8,
  LOCK_RELEASE = 4,
  LOCK_DECAY   = 1
) (
  clk, rst_n, enable, data_in,
  data_out, sample_strobe, clk_out, lock
);
```

- `clk` 是过采样时钟，频率 = `OVERSAMPLE × 数据率`。
- 捕获阶段：`data_edge` 检测后把相位计数器装到 `EDGE_PHASE`；锁定后相位自由运行，每 `OVERSAMPLE` 拍采样一次。
- `sample_strobe` 每比特脉冲一次，是**数据有效选通**；应使用它，而不是 `clk_out`，来接收 `data_out`。
- `clk_out` 是分频监视时钟，占空比 50%（`OVERSAMPLE` 为偶数）。
- `lock` 在连续检测到 `LOCK_THRESH` 个边沿后置位，在 `LOCK_RELEASE` 以下释放（滞回），避免帧间空隙导致误失锁。
- 约束：`OVERSAMPLE` 为偶数且 ≥ 4；`SYNC_STAGES ≥ 2`。这是频率锁定的过采样接收机，不含 plesiochronous 相位跟踪环。

## 3. 与 OpenSERDES 原实现的关键差异

| 原实现 | 抽象层 |
|---|---|
| Serializer 依赖 `READY` 边沿、`int_PAR` 多驱动且被综合删除 | 单 `clk` + `load/ready`，输入寄存器真正保留 |
| Deserializer 256 个 DFF 由 `COMPLETE` 直接打拍 | 单 `clk` + `enable`，无生成时钟 |
| Deserializer `4'd3` 分支缺少 `OUT[0] <= SERIAL_IN` | 按位索引写入，无 state-3 位滑移 |
| 300 个并行输出直接暴露 | `WORDS×DATA_WIDTH` 参数化数组，可由 generator 选择并行/串行配置 |
| DeSer `INT_PAR_IN` 没有正确传递 | 每个 word 单寄存器组，`par_data` 直连，无影子锁存 |
| CDR 只有门级网表、22 位 SCAN_IN 语义未文档化 | 可综合抽象 CDR，带明确 `sample_strobe/lock` 契约 |
| 原 Serializer 只有 13 个 DFF（输入寄存器被删），DeSer 更多 | 抽象后 Serializer 266 FF、DeSer 266 FF、Top 550 FF；单时钟 + `enable`，必须走 CTS；见 `docs/clocking.md` |
| 无 testbench | 11 个配置（serializer LSB/MSB/capture、deserializer LSB/MSB、loopback LSB/MSB、32×8、channel-model、CDR） |

## 4. 验证

```bash
make sim    # iverilog：serializer/deserializer/loopback/32×8/channel/CDR
make lint   # Verilator -Wall
make synth  # Yosys 通用门级检查
```

已验证：

- `tb_serializer` LSB/MSB：PASS
- `tb_deserializer` LSB/MSB：PASS
- `tb_serdes_loopback` LSB/MSB：PASS
- `tb_serdes_default` 32×8：PASS
- `tb_serdes_channel` TX→channel→CDR 串行恢复 + `par_valid`：PASS
- `tb_channel` delay/invert/error 注入：PASS
- `tb_cdr` 100 个恢复样本：PASS
- Verilator lint：0 warning
- Yosys synth：仅通用门/DFF，无 latch、无 memory

顶层/信道/SDC：

- `rtl/serdes_digital_top.sv`：数字顶层
- `tb/channel_model.sv`：延迟/反相/确定性误码注入的数字信道模型（不含模拟损耗/ISI）
- `constraints/serdes_digital.sdc`：TX/RX 双时钟、异步 clock group、uncertainty、I/O delay 模板（模板值未用目标 PDK 工具签核，生成器需替换周期/uncertainty）
- `docs/clocking.md`：原网表时钟树审计与 skew 预算（已按 3 级 CTS/均衡扇出修正）
- `docs/prepnr_sta.md`：pre-PnR STA 方法学、reset/control 分离、三种工艺分级与线延迟预算
- `tech/<pdk>/tech.yaml`：PDK/SCL/LIB/corner/CTS 目标；路径用 `${PDK_ROOT}` / `${PDK_LIB_ROOT}` 展开，见 `tech/README.md`
- `scripts/analyze_clock_tree.py`：从 `.lvs.v` 提取时钟树深度/扇出

## 5. 参数与目标工艺

`DATA_WIDTH`/`WORDS` 不绑定工艺；`RATIO=DATA_WIDTH*WORDS` 在模块内部派生，避免参数不一致。目标工艺由 generator 的 `tech.yaml` 选择：

```yaml
limits:
  digital_fmax_mhz: 500
  serial_mbps: 500
  cdr_sample_mhz: 500
  max_pads: 64
```

生成器规则：

- 1 µm/0.5 µm：`LS`，`WORDS=4/8`，`DATA_WIDTH=8/16`，source-synchronous。
- 130/180 nm：`MS`，`WORDS=8`，`DATA_WIDTH=32`，4× oversampling 或 source-sync。
- 55 nm/SiGe：`HS` 数字/模拟分离；55 nm 可 32-bit 并行，SiGe 模拟前端另做。

## 6. 下一层抽象（尚未实现，不在本目录承诺）

- Frame alignment/comma、8b10b/64b66b。
- Elastic FIFO、CDC（CDR 恢复时钟与本地时钟）。
- Scan/DFT、clock gating。
- 模拟 TX/RX 的 Verilog-A/RNM wrapper。
- 目标 PDK 的 cellmap/SDC/floorplan/signoff adapter。

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

## 8. 许可

原 OpenSERDES 为 GPL-3.0。本目录是行为/接口级的 clean rewrite，未复制原门级网表；对外分发前请确认 GPL 义务与目标 PDK 许可。
