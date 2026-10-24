# RX endpoint SDC. CLOCK_PERIOD_NS is replaced by the generator.
set CLOCK_PERIOD_NS 5.0
create_clock -name rx_clk -period $CLOCK_PERIOD_NS [get_ports rx_clk]
set_clock_uncertainty -setup 0.050 [get_clocks rx_clk]
set_clock_uncertainty -hold  0.020 [get_clocks rx_clk]

set_input_delay  -clock rx_clk 0.200 [get_ports {rx_serial_in rx_enable rx_flush rx_align}]
set_output_delay -clock rx_clk 0.200 [get_ports {rx_par_data* rx_par_valid rx_busy rx_lock rx_sample_strobe}]
set_false_path -to [get_ports rx_clk_out]

set_false_path -from [get_ports rx_enable]
set_false_path -from [get_ports rx_rst_n]
foreach pin [get_pins -hierarchical *RESET_B] { set_false_path -through $pin }
foreach pin [get_pins -hierarchical *RN]      { set_false_path -through $pin }
foreach pin [get_pins -hierarchical *SN]      { set_false_path -through $pin }
foreach pin [get_pins -hierarchical *SETN]    { set_false_path -through $pin }
foreach pin [get_pins -hierarchical *SET_B]   { set_false_path -through $pin }
