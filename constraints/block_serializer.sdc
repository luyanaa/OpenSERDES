# Serializer leaf-block SDC. CLOCK_PERIOD_NS is replaced by the generator.
set CLOCK_PERIOD_NS 20.0
create_clock -name clk -period $CLOCK_PERIOD_NS [get_ports clk]
set_clock_uncertainty -setup 0.050 [get_clocks clk]
set_clock_uncertainty -hold  0.020 [get_clocks clk]

set_input_delay  -clock clk 0.200 [get_ports {load enable par_data*}]
set_output_delay -clock clk 0.200 [get_ports {ready serial_out serial_valid busy frame_done}]

# Async reset is excluded from data-path STA; reset recovery/removal is
# analyzed after reset-tree insertion.
set_false_path -from [get_ports rst_n]
foreach pin [get_pins -hierarchical *RESET_B] { set_false_path -through $pin }
foreach pin [get_pins -hierarchical *RN]      { set_false_path -through $pin }
foreach pin [get_pins -hierarchical *SN]      { set_false_path -through $pin }
foreach pin [get_pins -hierarchical *SETN]    { set_false_path -through $pin }
foreach pin [get_pins -hierarchical *SET_B]   { set_false_path -through $pin }
