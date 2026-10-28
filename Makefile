IVERILOG ?= iverilog
VVP      ?= vvp
VERILATOR?= verilator
YOSYS    ?= yosys
SV2V     ?= sv2v
PYTHON   ?= python3
SPEC     ?= spec/default.yaml
OUT      ?= out/default
REPORT   ?= spec/qualification_report.example.json
LEVEL    ?= RTL_FUNCTIONAL
PACKAGE  ?= out/default
REPORTS  ?= $(PACKAGE)/reports
SIGNOFF_LEVEL ?= POST_ROUTE_TIMING
PPA_DB   ?= tech/ppa_measured.yaml
PPA_CSV  ?=
PPA_OUT  ?= tech/ppa_measured.yaml
VERIFY_PROFILE ?= verification/profiles/stressed.yaml
VERIFY_OUT     ?= $(BUILD)/verify/ber.json
VERIFY_PACKAGE ?=
VERIFY_ANALOG_LUT ?=
ANALOG_TECH ?= sky130
ANALOG_LUT ?= $(BUILD)/verify/$(ANALOG_TECH)_lut.json
SKY130_MODEL ?=
ANALOG_MODEL ?= $(SKY130_MODEL)
ANALOG_TX_LOADS ?= 10 50 100 200
ANALOG_RX_SLEWS ?= 20 50 100 200
PVT_CORNERS ?= tt ss ff sf fs
PVT_VOLTAGES ?= 1.62 1.80 1.98
PVT_TEMPERATURES ?= -40 25 100
PVT_OUT ?= $(BUILD)/verify/sky130_pvt_lut.json
PVT_MARGIN_OUT ?= $(BUILD)/verify/pvt_margin.json
TIMING_LIB ?=
TIMING_PERIOD_NS ?= 5.0
TIMING_PERIOD2_NS ?= 10.0
TIMING_OUT ?= $(BUILD)/timing_compare.json
MC_SAMPLES ?= 20
MC_OUT ?= $(BUILD)/verify/sky130_mc.json
VERIFY_TX_LOAD_FF ?= 50
VERIFY_JTOL_MIN_AMP_UI ?= 0.10
VERIFY_JTOL_AMPS ?= 0.05 0.10 0.20 0.40
VERIFY_BER_TARGET ?= 1e-3
SKY130_LUT ?= $(BUILD)/verify/sky130_lut.json
SKY130_TX_LOADS ?= 10 50 100 200
SKY130_RX_SLEWS ?= 20 50 100 200

BUILD := build
RTL   := $(wildcard rtl/*.sv)
SV2V_DIR := $(BUILD)/sv2v
SV2V_RTL := $(SV2V_DIR)/rtl.v

.PHONY: all sim lint synth regress gen qualify signoff ppa-import clean si-eye si-bathtub si-jtol si-rtl-jtol verify-link analog-lut sky130-lut analog-lut-demo analog-tech-check sky130-pvt pvt-margin sky130-mc pex-compare lib-catalog timing-compare

all: sim

$(BUILD):
	mkdir -p $(BUILD)

$(SV2V_RTL): $(RTL) | $(BUILD)
	@command -v $(SV2V) >/dev/null 2>&1 || { \
		echo "error: sv2v not found (install v0.0.12 or set SV2V=/path/to/sv2v)"; \
		echo "  CI installs it; native SystemVerilog is kept for simulation/lint"; \
		exit 1; }
	@mkdir -p $(SV2V_DIR)
	$(SV2V) $(RTL) > $@

$(BUILD)/tb_serializer_lsb: tb/tb_serializer.sv rtl/serializer.sv | $(BUILD)
	$(IVERILOG) -g2012 -Wall -DTB_LSB_FIRST=1 -o $@ $^

$(BUILD)/tb_serializer_msb: tb/tb_serializer.sv rtl/serializer.sv | $(BUILD)
	$(IVERILOG) -g2012 -Wall -DTB_LSB_FIRST=0 -o $@ $^

$(BUILD)/tb_serializer_capture: tb/tb_serializer.sv rtl/serializer.sv | $(BUILD)
	$(IVERILOG) -g2012 -Wall -DTB_LSB_FIRST=1 -DTB_CAPTURE_MODE=1 -o $@ $^

$(BUILD)/tb_serializer_stream: tb/tb_serializer_stream.sv rtl/serializer_stream.sv | $(BUILD)
	$(IVERILOG) -g2012 -Wall -o $@ $^

$(BUILD)/tb_serializer_hier_2to1: tb/tb_serializer_hier_2to1.sv rtl/serializer_stream.sv rtl/serializer_hier_2to1.sv | $(BUILD)
	$(IVERILOG) -g2012 -Wall -o $@ $^

$(BUILD)/tb_serializer_half_rate: tb/tb_serializer_half_rate.sv rtl/serializer_stream.sv rtl/serializer_half_rate.sv | $(BUILD)
	$(IVERILOG) -g2012 -Wall -o $@ $^

$(BUILD)/tb_deserializer_half_rate: tb/tb_deserializer_half_rate.sv rtl/deserializer_half_rate.sv | $(BUILD)
	$(IVERILOG) -g2012 -Wall -o $@ $^

$(BUILD)/tb_receiver_pair_sampler: tb/tb_receiver_pair_sampler.sv rtl/receiver_pair_sampler.sv | $(BUILD)
	$(IVERILOG) -g2012 -Wall -o $@ $^

$(BUILD)/tb_deserializer_lsb: tb/tb_deserializer.sv rtl/deserializer_word.sv rtl/deserializer.sv | $(BUILD)
	$(IVERILOG) -g2012 -Wall -DTB_LSB_FIRST=1 -o $@ $^

$(BUILD)/tb_deserializer_msb: tb/tb_deserializer.sv rtl/deserializer_word.sv rtl/deserializer.sv | $(BUILD)
	$(IVERILOG) -g2012 -Wall -DTB_LSB_FIRST=0 -o $@ $^

$(BUILD)/tb_serdes_loopback_lsb: tb/tb_serdes_loopback.sv rtl/serializer.sv rtl/deserializer_word.sv rtl/deserializer.sv | $(BUILD)
	$(IVERILOG) -g2012 -Wall -DTB_LSB_FIRST=1 -o $@ $^

$(BUILD)/tb_serdes_loopback_msb: tb/tb_serdes_loopback.sv rtl/serializer.sv rtl/deserializer_word.sv rtl/deserializer.sv | $(BUILD)
	$(IVERILOG) -g2012 -Wall -DTB_LSB_FIRST=0 -o $@ $^

$(BUILD)/tb_serdes_default: tb/tb_serdes_default.sv rtl/serializer.sv rtl/deserializer_word.sv rtl/deserializer.sv | $(BUILD)
	$(IVERILOG) -g2012 -Wall -o $@ $^

$(BUILD)/tb_serdes_channel: tb/tb_serdes_channel.sv tb/channel_model.sv $(RTL) | $(BUILD)
	$(IVERILOG) -g2012 -Wall -o $@ $^

$(BUILD)/tb_channel: tb/tb_channel.sv tb/channel_model.sv | $(BUILD)
	$(IVERILOG) -g2012 -Wall -o $@ $^

$(BUILD)/tb_cdr: tb/tb_cdr.sv rtl/cdr_oversampling.sv rtl/cdr_sync.sv | $(BUILD)
	$(IVERILOG) -g2012 -Wall -o $@ $^

$(BUILD)/tb_cdr_adaptive: tb/tb_cdr_adaptive.sv rtl/cdr_adaptive.sv rtl/cdr_sync.sv | $(BUILD)
	$(IVERILOG) -g2012 -Wall -o $@ $^

$(BUILD)/tb_frame_sync: tb/tb_frame_sync.sv rtl/comma_align.sv rtl/deserializer.sv rtl/deserializer_word.sv | $(BUILD)
	$(IVERILOG) -g2012 -Wall -o $@ $^

$(BUILD)/tb_cdr_bypass: tb/tb_cdr_bypass.sv $(RTL) | $(BUILD)
	$(IVERILOG) -g2012 -Wall -o $@ $^

$(BUILD)/tb_compat_legacy: tb/tb_compat_legacy.sv rtl/serializer.sv rtl/deserializer_word.sv rtl/deserializer.sv rtl/compat/serializer_unit_cell_1.v rtl/compat/deserialiser_unit_cell_1.v | $(BUILD)
	$(IVERILOG) -g2012 -Wall -o $@ $^

$(BUILD)/tb_compat_port_order: tb/tb_compat_port_order.sv rtl/serializer.sv rtl/deserializer_word.sv rtl/deserializer.sv rtl/compat/serializer_unit_cell_1.v rtl/compat/deserialiser_unit_cell_1.v | $(BUILD)
	$(IVERILOG) -g2012 -Wall -o $@ $^

$(BUILD)/tb_apb_csr: tb/tb_apb_csr.sv rtl/apb_csr.sv | $(BUILD)
	$(IVERILOG) -g2012 -Wall -o $@ $^

$(BUILD)/tb_async_fifo: tb/tb_async_fifo.sv rtl/async_fifo.sv | $(BUILD)
	$(IVERILOG) -g2012 -Wall -o $@ $^

$(BUILD)/tb_axis_tx_adapter: tb/tb_axis_tx_adapter.sv rtl/axis_tx_adapter.sv | $(BUILD)
	$(IVERILOG) -g2012 -Wall -o $@ $^

$(BUILD)/tb_axis_rx_adapter: tb/tb_axis_rx_adapter.sv rtl/axis_rx_adapter.sv | $(BUILD)
	$(IVERILOG) -g2012 -Wall -o $@ $^

$(BUILD)/tb_serdes_tx_axis: tb/tb_serdes_tx_axis.sv rtl/serdes_tx_axis_digital.sv rtl/axis_tx_adapter.sv rtl/serializer_stream.sv rtl/reset_sync.sv | $(BUILD)
	$(IVERILOG) -g2012 -Wall -o $@ $^

$(BUILD)/tb_serdes_rx_axis: tb/tb_serdes_rx_axis.sv $(RTL) | $(BUILD)
	$(IVERILOG) -g2012 -Wall -o $@ $^

$(BUILD)/tb_prbs_7: tb/tb_prbs.sv rtl/prbs_gen.sv rtl/prbs_check.sv | $(BUILD)
	$(IVERILOG) -g2012 -Wall -Ptb_prbs.WIDTH=7 -o $@ $^

$(BUILD)/tb_prbs_15: tb/tb_prbs.sv rtl/prbs_gen.sv rtl/prbs_check.sv | $(BUILD)
	$(IVERILOG) -g2012 -Wall -Ptb_prbs.WIDTH=15 -o $@ $^

$(BUILD)/tb_prbs_31: tb/tb_prbs.sv rtl/prbs_gen.sv rtl/prbs_check.sv | $(BUILD)
	$(IVERILOG) -g2012 -Wall -Ptb_prbs.WIDTH=31 -o $@ $^

$(BUILD)/tb_serdes_soc_wrapper: tb/tb_serdes_soc_wrapper.sv $(RTL) | $(BUILD)
	$(IVERILOG) -g2012 -Wall -o $@ $^

$(BUILD)/tb_serdes_soc_cdc_wrapper: tb/tb_serdes_soc_cdc_wrapper.sv $(RTL) | $(BUILD)
	$(IVERILOG) -g2012 -Wall -o $@ $^

$(BUILD)/tb_serdes_bist_7: tb/tb_serdes_bist.sv rtl/serdes_bist.sv rtl/prbs_gen.sv rtl/prbs_check.sv | $(BUILD)
	$(IVERILOG) -g2012 -Wall -Ptb_serdes_bist.WIDTH=7 -o $@ $^

$(BUILD)/tb_serdes_bist_15: tb/tb_serdes_bist.sv rtl/serdes_bist.sv rtl/prbs_gen.sv rtl/prbs_check.sv | $(BUILD)
	$(IVERILOG) -g2012 -Wall -Ptb_serdes_bist.WIDTH=15 -o $@ $^

$(BUILD)/tb_serdes_bist_31: tb/tb_serdes_bist.sv rtl/serdes_bist.sv rtl/prbs_gen.sv rtl/prbs_check.sv | $(BUILD)
	$(IVERILOG) -g2012 -Wall -Ptb_serdes_bist.WIDTH=31 -o $@ $^

$(BUILD)/tb_serdes_bist_word: tb/tb_serdes_bist_word.sv rtl/prbs_word_gen.sv rtl/prbs_word_check.sv rtl/serializer_stream.sv rtl/deserializer.sv rtl/deserializer_word.sv | $(BUILD)
	$(IVERILOG) -g2012 -Wall -o $@ $^

SIM_BINS := $(BUILD)/tb_serializer_lsb $(BUILD)/tb_serializer_msb \
            $(BUILD)/tb_serializer_capture $(BUILD)/tb_serializer_stream \
            $(BUILD)/tb_serializer_hier_2to1 $(BUILD)/tb_serializer_half_rate \
            $(BUILD)/tb_deserializer_half_rate $(BUILD)/tb_receiver_pair_sampler \
            $(BUILD)/tb_deserializer_lsb $(BUILD)/tb_deserializer_msb \
            $(BUILD)/tb_serdes_loopback_lsb $(BUILD)/tb_serdes_loopback_msb \
            $(BUILD)/tb_serdes_default $(BUILD)/tb_serdes_channel $(BUILD)/tb_channel \
            $(BUILD)/tb_cdr $(BUILD)/tb_cdr_adaptive $(BUILD)/tb_frame_sync \
            $(BUILD)/tb_cdr_bypass $(BUILD)/tb_compat_legacy $(BUILD)/tb_compat_port_order \
            $(BUILD)/tb_apb_csr $(BUILD)/tb_async_fifo \
            $(BUILD)/tb_axis_tx_adapter $(BUILD)/tb_axis_rx_adapter \
            $(BUILD)/tb_serdes_tx_axis $(BUILD)/tb_serdes_rx_axis \
            $(BUILD)/tb_prbs_7 $(BUILD)/tb_prbs_15 $(BUILD)/tb_prbs_31 \
            $(BUILD)/tb_serdes_soc_wrapper $(BUILD)/tb_serdes_soc_cdc_wrapper \
            $(BUILD)/tb_serdes_bist_7 $(BUILD)/tb_serdes_bist_15 $(BUILD)/tb_serdes_bist_31 \
            $(BUILD)/tb_serdes_bist_word

sim: $(SIM_BINS)
	$(VVP) $(BUILD)/tb_serializer_lsb
	$(VVP) $(BUILD)/tb_serializer_msb
	$(VVP) $(BUILD)/tb_serializer_capture
	$(VVP) $(BUILD)/tb_serializer_stream
	$(VVP) $(BUILD)/tb_serializer_hier_2to1
	$(VVP) $(BUILD)/tb_serializer_half_rate
	$(VVP) $(BUILD)/tb_deserializer_half_rate
	$(VVP) $(BUILD)/tb_receiver_pair_sampler
	$(VVP) $(BUILD)/tb_deserializer_lsb
	$(VVP) $(BUILD)/tb_deserializer_msb
	$(VVP) $(BUILD)/tb_serdes_loopback_lsb
	$(VVP) $(BUILD)/tb_serdes_loopback_msb
	$(VVP) $(BUILD)/tb_serdes_default
	$(VVP) $(BUILD)/tb_serdes_channel
	$(VVP) $(BUILD)/tb_channel
	$(VVP) $(BUILD)/tb_cdr
	$(VVP) $(BUILD)/tb_cdr_adaptive
	$(VVP) $(BUILD)/tb_frame_sync
	$(VVP) $(BUILD)/tb_cdr_bypass
	$(VVP) $(BUILD)/tb_compat_legacy
	$(VVP) $(BUILD)/tb_compat_port_order
	$(VVP) $(BUILD)/tb_apb_csr
	$(VVP) $(BUILD)/tb_async_fifo
	$(VVP) $(BUILD)/tb_axis_tx_adapter
	$(VVP) $(BUILD)/tb_axis_rx_adapter
	$(VVP) $(BUILD)/tb_serdes_tx_axis
	$(VVP) $(BUILD)/tb_serdes_rx_axis
	$(VVP) $(BUILD)/tb_prbs_7
	$(VVP) $(BUILD)/tb_prbs_15
	$(VVP) $(BUILD)/tb_prbs_31
	$(VVP) $(BUILD)/tb_serdes_soc_wrapper
	$(VVP) $(BUILD)/tb_serdes_soc_cdc_wrapper
	$(VVP) $(BUILD)/tb_serdes_bist_7
	$(VVP) $(BUILD)/tb_serdes_bist_15
	$(VVP) $(BUILD)/tb_serdes_bist_31
	$(VVP) $(BUILD)/tb_serdes_bist_word

lint:
	$(VERILATOR) --lint-only -Wall --top-module serdes_digital_top $(RTL)
	$(VERILATOR) --lint-only -Wall --top-module serdes_tx_digital $(RTL)
	$(VERILATOR) --lint-only -Wall --top-module serdes_tx_stream_digital $(RTL)
	$(VERILATOR) --lint-only -Wall --top-module serdes_rx_digital $(RTL)
	$(VERILATOR) --lint-only -Wall --top-module cdr_oversampling $(RTL)
	$(VERILATOR) --lint-only -Wall --top-module cdr_adaptive $(RTL)
	$(VERILATOR) --lint-only -Wall --top-module serializer_stream $(RTL)
	$(VERILATOR) --lint-only -Wall --top-module serializer_hier_2to1 $(RTL)
	$(VERILATOR) --lint-only -Wall --top-module serializer_half_rate $(RTL)
	$(VERILATOR) --lint-only -Wall --top-module deserializer_half_rate $(RTL)
	$(VERILATOR) --lint-only -Wall --top-module receiver_pair_sampler $(RTL)
	$(VERILATOR) --lint-only -Wall --top-module comma_align $(RTL)
	$(VERILATOR) --lint-only -Wall --top-module apb_csr $(RTL)
	$(VERILATOR) --lint-only -Wall --top-module async_fifo $(RTL)
	$(VERILATOR) --lint-only -Wall --top-module axis_tx_adapter $(RTL)
	$(VERILATOR) --lint-only -Wall --top-module axis_rx_adapter $(RTL)
	$(VERILATOR) --lint-only -Wall --top-module serdes_tx_axis_digital $(RTL)
	$(VERILATOR) --lint-only -Wall --top-module serdes_rx_axis_digital $(RTL)
	$(VERILATOR) --lint-only -Wall --top-module prbs_gen $(RTL)
	$(VERILATOR) --lint-only -Wall --top-module prbs_check $(RTL)
	$(VERILATOR) --lint-only -Wall --top-module serdes_soc_wrapper $(RTL)
	$(VERILATOR) --lint-only -Wall --top-module serdes_soc_cdc_wrapper $(RTL)
	$(VERILATOR) --lint-only -Wall --top-module serdes_bist $(RTL)
	$(VERILATOR) --lint-only -Wall --top-module bist_snapshot $(RTL)
	$(VERILATOR) --lint-only -Wall --top-module prbs_word_gen rtl/prbs_word_gen.sv
	$(VERILATOR) --lint-only -Wall --top-module prbs_word_check rtl/prbs_word_check.sv
	$(VERILATOR) --lint-only -Wall --top-module serializer_unit_cell_1 rtl/serializer.sv rtl/compat/serializer_unit_cell_1.v
	$(VERILATOR) --lint-only -Wall --top-module deserialiser_unit_cell_1 rtl/deserializer_word.sv rtl/deserializer.sv rtl/compat/deserialiser_unit_cell_1.v

synth: $(SV2V_RTL)
	$(YOSYS) -q -p "read_verilog $(SV2V_RTL); synth -top serializer; stat"
	$(YOSYS) -q -p "read_verilog $(SV2V_RTL); synth -top serializer_stream; stat"
	$(YOSYS) -q -p "read_verilog $(SV2V_RTL); synth -top serializer_hier_2to1; stat"
	$(YOSYS) -q -p "read_verilog $(SV2V_RTL); synth -top serializer_half_rate; stat"
	$(YOSYS) -q -p "read_verilog $(SV2V_RTL); synth -top deserializer_half_rate; stat"
	$(YOSYS) -q -p "read_verilog $(SV2V_RTL); synth -top receiver_pair_sampler; stat"
	$(YOSYS) -q -p "read_verilog $(SV2V_RTL); synth -top deserializer; stat"
	$(YOSYS) -q -p "read_verilog $(SV2V_RTL); synth -top cdr_oversampling; stat"
	$(YOSYS) -q -p "read_verilog $(SV2V_RTL); synth -top cdr_adaptive; stat"
	$(YOSYS) -q -p "read_verilog $(SV2V_RTL); synth -top apb_csr; stat"
	$(YOSYS) -q -p "read_verilog $(SV2V_RTL); synth -top async_fifo; stat"
	$(YOSYS) -q -p "read_verilog $(SV2V_RTL); synth -top axis_tx_adapter; stat"
	$(YOSYS) -q -p "read_verilog $(SV2V_RTL); synth -top axis_rx_adapter; stat"
	$(YOSYS) -q -p "read_verilog $(SV2V_RTL); synth -top serdes_tx_axis_digital; stat"
	$(YOSYS) -q -p "read_verilog $(SV2V_RTL); synth -top serdes_rx_axis_digital; stat"
	$(YOSYS) -q -p "read_verilog $(SV2V_RTL); synth -top prbs_check; stat"
	$(YOSYS) -q -p "read_verilog $(SV2V_RTL); synth -top serdes_soc_wrapper; stat"
	$(YOSYS) -q -p "read_verilog $(SV2V_RTL); synth -top serdes_soc_cdc_wrapper; stat"
	$(YOSYS) -q -p "read_verilog $(SV2V_RTL); synth -top serdes_bist; stat"
	$(YOSYS) -q -p "read_verilog $(SV2V_RTL); synth -top bist_snapshot; stat"
	$(YOSYS) -q -p "read_verilog $(SV2V_RTL); synth -top prbs_word_check; stat"
	$(YOSYS) -q -p "read_verilog $(SV2V_RTL); synth -top serdes_digital_top; stat"

analog-lut:
	$(PYTHON) verification/analog/extract_lut.py --tech $(ANALOG_TECH) \
		$(if $(ANALOG_MODEL),--model $(ANALOG_MODEL),) \
		--tx-loads-ff $(ANALOG_TX_LOADS) --rx-slews-ps $(ANALOG_RX_SLEWS) --out $(ANALOG_LUT)

sky130-lut:
	$(PYTHON) verification/analog/extract_lut.py --tech sky130 \
		$(if $(ANALOG_MODEL),--model $(ANALOG_MODEL),) \
		--tx-loads-ff $(SKY130_TX_LOADS) --rx-slews-ps $(SKY130_RX_SLEWS) --out $(SKY130_LUT)

analog-lut-demo:
	$(PYTHON) verification/analog/extract_lut.py --tech generic_demo --out $(BUILD)/verify/analog_lut_demo.json

sky130-pvt:
	$(PYTHON) verification/analog/pvt_runner.py --tech sky130 \
		--corners $(PVT_CORNERS) --voltages $(PVT_VOLTAGES) \
		--temperatures $(PVT_TEMPERATURES) --out $(PVT_OUT)

sky130-mc:
	$(PYTHON) verification/analog/mc_runner.py --tech sky130 --samples $(MC_SAMPLES) --out $(MC_OUT)

pex-compare:
	@test -n "$(SCHEM_LUT)" -a -n "$(PEX_LUT)" || { \
		echo "error: set SCHEM_LUT=<lut.json> PEX_LUT=<pex_lut.json>"; exit 1; }
	$(PYTHON) verification/analog/pex_characterize.py \
		--schematic-lut $(SCHEM_LUT) --pex-lut $(PEX_LUT) --out $(PEX_OUT)

timing-compare:
	@test -n "$(TIMING_LIB)" || { \
		echo "error: set TIMING_LIB=<liberty file>"; exit 1; }
	$(PYTHON) scripts/compare_timing.py --liberty $(TIMING_LIB) \
		--tops serializer_stream serializer_hier_2to1 \
		--period-ns $(TIMING_PERIOD_NS) --out $(TIMING_OUT)
	$(PYTHON) scripts/compare_timing.py --liberty $(TIMING_LIB) \
		--tops serializer_half_rate --period-ns $(TIMING_PERIOD_NS) \
		--clock-port clk_bit --clock-port2 clk_word \
		--period2-ns $(TIMING_PERIOD2_NS) --out $(TIMING_OUT).half
	$(PYTHON) scripts/compare_timing.py --liberty $(TIMING_LIB) \
		--tops deserializer --period-ns $(TIMING_PERIOD_NS) \
		--rtl rtl/deserializer.sv --extra-rtl rtl/deserializer_word.sv \
		--clock-port clk --out $(TIMING_OUT).rx_base
	$(PYTHON) scripts/compare_timing.py --liberty $(TIMING_LIB) \
		--tops deserializer_half_rate --period-ns $(TIMING_PERIOD2_NS) \
		--clock-port clk_word --out $(TIMING_OUT).rx_half

lib-catalog:
	$(PYTHON) scripts/library_catalog.py --out $(BUILD)/library_catalog.json --markdown $(BUILD)/library_catalog.md

pvt-margin:
	$(PYTHON) scripts/pvt_margin.py --pvt $(PVT_OUT) --out $(PVT_MARGIN_OUT)

analog-tech-check:
	$(PYTHON) verification/analog/extract_lut.py --tech $(ANALOG_TECH) --check-tools --out $(BUILD)/verify/$(ANALOG_TECH)_tech_check.json

verify-link:
	@test -n "$(VERIFY_PACKAGE)" || { \
		echo "error: verify-link requires VERIFY_PACKAGE=<generated-package>"; \
		echo "  unbound reports cannot declare link capability"; \
		exit 1; }
	$(PYTHON) scripts/verify_link.py --profile $(VERIFY_PROFILE) --out $(VERIFY_OUT) \
		$(if $(VERIFY_PACKAGE),--package $(VERIFY_PACKAGE),) \
		$(if $(VERIFY_ANALOG_LUT),--analog-lut $(VERIFY_ANALOG_LUT) --tx-load-ff $(VERIFY_TX_LOAD_FF),) \
		--jtol-min-amp-ui $(VERIFY_JTOL_MIN_AMP_UI) --jtol-amps $(VERIFY_JTOL_AMPS) \
		--ber-target $(VERIFY_BER_TARGET)

si-eye:
	$(PYTHON) -m verification.cli eye --profile verification/profiles/stressed.yaml --out $(BUILD)/verify/eye.png

si-bathtub:
	$(PYTHON) -m verification.cli bathtub --profile verification/profiles/stressed.yaml --out $(BUILD)/verify/bathtub.png --json $(BUILD)/verify/bathtub.json

si-jtol:
	$(PYTHON) -m verification.cli jtol --profile verification/profiles/stressed.yaml --out $(BUILD)/verify/jtol.json --model analytic

si-rtl-jtol:
	$(PYTHON) -m verification.rtl.runner --nbits 800 --sj-peak-ui 0.2 --sj-freq-hz 500000

regress:
	@$(PYTHON) -c "import yaml" >/dev/null 2>&1 || { \
		echo "error: $(PYTHON) cannot import PyYAML"; \
		echo "  install dependencies: $(PYTHON) -m pip install -r requirements.txt"; \
		echo "  or select a system interpreter: make regress PYTHON=/usr/bin/python3"; \
		exit 1; }
	$(PYTHON) scripts/regress.py

gen:
	SV2V=$(SV2V) $(PYTHON) scripts/gen_ip.py --spec $(SPEC) --out $(OUT) --force --ppa-db $(PPA_DB)

qualify:
	$(PYTHON) scripts/qualify.py --report $(REPORT) --level $(LEVEL)

signoff:
	$(PYTHON) scripts/collect_signoff.py --package $(PACKAGE) --reports $(REPORTS) --requested-level $(SIGNOFF_LEVEL)

ppa-import:
	$(PYTHON) scripts/import_ppa.py --csv $(PPA_CSV) --out $(PPA_OUT)

clean:
	rm -rf $(BUILD) out
