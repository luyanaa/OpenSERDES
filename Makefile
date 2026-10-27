IVERILOG ?= iverilog
VVP      ?= vvp
VERILATOR?= verilator
YOSYS    ?= yosys
PYTHON   ?= python3
SPEC     ?= spec/default.yaml
OUT      ?= out/default
REPORT   ?= spec/qualification_report.example.json
LEVEL    ?= RTL_FUNCTIONAL

BUILD := build
RTL   := $(wildcard rtl/*.sv)

.PHONY: all sim lint synth regress gen qualify clean

all: sim

$(BUILD):
	mkdir -p $(BUILD)

$(BUILD)/tb_serializer_lsb: tb/tb_serializer.sv rtl/serializer.sv | $(BUILD)
	$(IVERILOG) -g2012 -Wall -DTB_LSB_FIRST=1 -o $@ $^

$(BUILD)/tb_serializer_msb: tb/tb_serializer.sv rtl/serializer.sv | $(BUILD)
	$(IVERILOG) -g2012 -Wall -DTB_LSB_FIRST=0 -o $@ $^

$(BUILD)/tb_serializer_capture: tb/tb_serializer.sv rtl/serializer.sv | $(BUILD)
	$(IVERILOG) -g2012 -Wall -DTB_LSB_FIRST=1 -DTB_CAPTURE_MODE=1 -o $@ $^

$(BUILD)/tb_serializer_stream: tb/tb_serializer_stream.sv rtl/serializer_stream.sv | $(BUILD)
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

SIM_BINS := $(BUILD)/tb_serializer_lsb $(BUILD)/tb_serializer_msb \
            $(BUILD)/tb_serializer_capture $(BUILD)/tb_serializer_stream \
            $(BUILD)/tb_deserializer_lsb $(BUILD)/tb_deserializer_msb \
            $(BUILD)/tb_serdes_loopback_lsb $(BUILD)/tb_serdes_loopback_msb \
            $(BUILD)/tb_serdes_default $(BUILD)/tb_serdes_channel $(BUILD)/tb_channel \
            $(BUILD)/tb_cdr $(BUILD)/tb_cdr_adaptive $(BUILD)/tb_frame_sync \
            $(BUILD)/tb_cdr_bypass $(BUILD)/tb_compat_legacy $(BUILD)/tb_compat_port_order

sim: $(SIM_BINS)
	$(VVP) $(BUILD)/tb_serializer_lsb
	$(VVP) $(BUILD)/tb_serializer_msb
	$(VVP) $(BUILD)/tb_serializer_capture
	$(VVP) $(BUILD)/tb_serializer_stream
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

lint:
	$(VERILATOR) --lint-only -Wall --top-module serdes_digital_top $(RTL)
	$(VERILATOR) --lint-only -Wall --top-module serdes_tx_digital $(RTL)
	$(VERILATOR) --lint-only -Wall --top-module serdes_tx_stream_digital $(RTL)
	$(VERILATOR) --lint-only -Wall --top-module serdes_rx_digital $(RTL)
	$(VERILATOR) --lint-only -Wall --top-module cdr_oversampling $(RTL)
	$(VERILATOR) --lint-only -Wall --top-module cdr_adaptive $(RTL)
	$(VERILATOR) --lint-only -Wall --top-module serializer_stream $(RTL)
	$(VERILATOR) --lint-only -Wall --top-module comma_align $(RTL)
	$(VERILATOR) --lint-only -Wall --top-module serializer_unit_cell_1 rtl/serializer.sv rtl/compat/serializer_unit_cell_1.v
	$(VERILATOR) --lint-only -Wall --top-module deserialiser_unit_cell_1 rtl/deserializer_word.sv rtl/deserializer.sv rtl/compat/deserialiser_unit_cell_1.v

synth:
	$(YOSYS) -q -p "read_verilog -sv rtl/serializer.sv; synth -top serializer; stat"
	$(YOSYS) -q -p "read_verilog -sv rtl/serializer_stream.sv; synth -top serializer_stream; stat"
	$(YOSYS) -q -p "read_verilog -sv rtl/deserializer_word.sv rtl/deserializer.sv; synth -top deserializer; stat"
	$(YOSYS) -q -p "read_verilog -sv rtl/cdr_sync.sv rtl/cdr_oversampling.sv; synth -top cdr_oversampling; stat"
	$(YOSYS) -q -p "read_verilog -sv rtl/cdr_sync.sv rtl/cdr_adaptive.sv; synth -top cdr_adaptive; stat"
	$(YOSYS) -q -p "read_verilog -sv $(RTL); synth -top serdes_digital_top; stat"

regress:
	$(PYTHON) scripts/regress.py

gen:
	$(PYTHON) scripts/gen_ip.py --spec $(SPEC) --out $(OUT) --force

qualify:
	$(PYTHON) scripts/qualify.py --report $(REPORT) --level $(LEVEL)

clean:
	rm -rf $(BUILD) out
