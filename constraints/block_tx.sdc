# TX endpoint SDC. CLOCK_PERIOD_NS is replaced by the generator.
set CLOCK_PERIOD_NS 20.0
create_clock -name tx_clk -period $CLOCK_PERIOD_NS [get_ports tx_clk]
set_clock_uncertainty -setup 0.050 [get_clocks tx_clk]
set_clock_uncertainty -hold  0.020 [get_clocks tx_clk]

set_input_delay  -clock tx_clk 0.200 [get_ports {tx_load tx_enable tx_par_data*}]
set_output_delay -clock tx_clk 0.200 [get_ports {tx_ready tx_serial_out tx_serial_valid tx_busy tx_frame_done}]

set_false_path -from [get_ports tx_enable]
set_false_path -from [get_ports tx_rst_n]
foreach pin [get_pins -hierarchical *RESET_B] { set_false_path -through $pin }
foreach pin [get_pins -hierarchical *RN]      { set_false_path -through $pin }
foreach pin [get_pins -hierarchical *SN]      { set_false_path -through $pin }
foreach pin [get_pins -hierarchical *SETN]    { set_false_path -through $pin }
foreach pin [get_pins -hierarchical *SET_B]   { set_false_path -through $pin }
