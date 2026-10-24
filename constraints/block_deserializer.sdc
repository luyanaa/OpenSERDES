# Deserializer leaf-block SDC. CLOCK_PERIOD_NS is replaced by the generator.
set CLOCK_PERIOD_NS 20.0
create_clock -name clk -period $CLOCK_PERIOD_NS [get_ports clk]
set_clock_uncertainty -setup 0.050 [get_clocks clk]
set_clock_uncertainty -hold  0.020 [get_clocks clk]

set_input_delay  -clock clk 0.200 [get_ports {serial_in serial_valid enable align flush}]
set_output_delay -clock clk 0.200 [get_ports {par_data* par_valid busy}]

# Async reset is excluded from data-path STA; reset recovery/removal is
# analyzed after reset-tree insertion.
set_false_path -from [get_ports rst_n]
foreach pin [get_pins -hierarchical *RESET_B] { set_false_path -through $pin }
foreach pin [get_pins -hierarchical *RN]      { set_false_path -through $pin }
foreach pin [get_pins -hierarchical *SN]      { set_false_path -through $pin }
foreach pin [get_pins -hierarchical *SETN]    { set_false_path -through $pin }
foreach pin [get_pins -hierarchical *SET_B]   { set_false_path -through $pin }
