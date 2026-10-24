# -----------------------------------------------------------------------------
# Example SDC for serdes_digital_top.
#
# These numbers are a template for a 25 Mbps link with 4x oversampling:
#   tx_clk = 25 MHz (40 ns)
#   rx_clk = 100 MHz (10 ns)
# Replace TX_PERIOD_NS with the target technology/rate before signoff.
#
# The design has two asynchronous clock domains.  There is no generated clock:
# CDR sample_strobe is a data enable, not a clock.  rx_clk_out is a monitor
# output and is explicitly excluded from timing.
# -----------------------------------------------------------------------------

set TX_PERIOD_NS 40.0
set OVERSAMPLE   4
set RX_PERIOD_NS [expr {$TX_PERIOD_NS / $OVERSAMPLE}]

create_clock -name tx_clk -period $TX_PERIOD_NS [get_ports tx_clk]
create_clock -name rx_clk -period $RX_PERIOD_NS [get_ports rx_clk]

set_clock_latency -source 0.0 [get_clocks tx_clk]
set_clock_latency -source 0.0 [get_clocks rx_clk]

# Setup/hold uncertainty includes CTS skew and jitter.  For a 2-level tree on
# a small 13-FF block these numbers are conservative; for the 256+ FF
# deserializer, CTS must be balanced and these values re-derived.
set_clock_uncertainty -setup 0.100 [get_clocks tx_clk]
set_clock_uncertainty -hold  0.050 [get_clocks tx_clk]
set_clock_uncertainty -setup 0.050 [get_clocks rx_clk]
set_clock_uncertainty -hold  0.030 [get_clocks rx_clk]

# The two domains are asynchronous.  The deserializer is intentionally
# self-contained in rx_clk; there is no CDC path inside this top.
set_clock_groups -asynchronous \
    -group [get_clocks tx_clk] \
    -group [get_clocks rx_clk]

# TX parallel interface (source-synchronous assumption).
set_input_delay  -clock tx_clk 0.200 [get_ports tx_par_data*]
set_input_delay  -clock tx_clk 0.200 [get_ports tx_load]
set_input_delay  -clock tx_clk 0.200 [get_ports tx_enable]
set_output_delay -clock tx_clk 0.200 [get_ports tx_ready]
set_output_delay -clock tx_clk 0.200 [get_ports tx_serial_out]
set_output_delay -clock tx_clk 0.200 [get_ports tx_serial_valid]
set_output_delay -clock tx_clk 0.200 [get_ports tx_busy]
set_output_delay -clock tx_clk 0.200 [get_ports tx_frame_done]

# RX parallel interface, referenced to rx_clk for this template.  A real
# chip-level integration should use the actual downstream clock.
set_input_delay  -clock rx_clk 0.200 [get_ports rx_serial_in]
set_input_delay  -clock rx_clk 0.200 [get_ports rx_enable]
set_input_delay  -clock rx_clk 0.200 [get_ports rx_flush]
set_input_delay  -clock rx_clk 0.200 [get_ports rx_align]
set_output_delay -clock rx_clk 0.200 [get_ports rx_par_data*]
set_output_delay -clock rx_clk 0.200 [get_ports rx_par_valid]
set_output_delay -clock rx_clk 0.200 [get_ports rx_busy]
set_output_delay -clock rx_clk 0.200 [get_ports rx_lock]
set_output_delay -clock rx_clk 0.200 [get_ports rx_sample_strobe]

# Monitor-only output; not a functional timing path.
set_false_path -to [get_ports rx_clk_out]
set_false_path -from [get_ports {tx_enable rx_enable}]

# Pre-CTS exception: the reset synchronizer output drives all async reset pins
# with no reset buffer tree yet.  Re-enable recovery/removal checks after the
# reset tree is inserted and buffered.
foreach pin [get_pins -hierarchical *RESET_B] { set_false_path -through $pin }
foreach pin [get_pins -hierarchical *RN]      { set_false_path -through $pin }
foreach pin [get_pins -hierarchical *SN]      { set_false_path -through $pin }
foreach pin [get_pins -hierarchical *SETN]    { set_false_path -through $pin }
foreach pin [get_pins -hierarchical *SET_B]   { set_false_path -through $pin }

# Clock net electrical limits (template values; replace with the PDK limits).
set_max_transition 0.200 [get_clocks tx_clk]
set_max_transition 0.200 [get_clocks rx_clk]
# set_max_capacitance / set_max_fanout are tool-specific in OpenSTA and are
# intentionally left to the CTS/PDK script rather than applied to Clock objects.

# Serial output load (template; replace with package/channel model).
set_load 0.010 [get_ports tx_serial_out]
