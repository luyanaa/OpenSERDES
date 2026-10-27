`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// Digital top level assembled from independently synthesizable TX and RX
// endpoint wrappers.  The same RTL can be instantiated with different PDK
// standard-cell libraries for TX and RX; only the wrappers and cell mappings
// change, not the protocol.
//
//   tx_clk : bit clock for the serializer.
//   rx_clk : oversampling clock for the CDR; frequency = OVERSAMPLE * data rate
//            in fixed/adaptive mode, or the recovered/forwarded bit clock in
//            source-synchronous mode.
//
// RX status:
//   rx_lock         : CDR has seen enough edges / source-sync mode.
//   rx_lock_stale   : no edge for LOCK_STALE_BITS bit periods (warning only).
//   rx_frame_locked : comma/training alignment acquired (ALIGN_MODE=1).
//   rx_serial_data  : recovered serial stream used by the deserializer.
// -----------------------------------------------------------------------------
module serdes_digital_top #(
    parameter int DATA_WIDTH      = 32,
    parameter int WORDS           = 8,
    parameter bit LSB_FIRST       = 1'b1,
    parameter bit CAPTURE_MODE    = 1'b0,
    parameter int OVERSAMPLE      = 4,
    parameter int SYNC_STAGES     = 3,
    parameter int LOCK_THRESH     = 8,
    parameter int LOCK_STALE_BITS = 16,
    parameter int CDR_MODE        = 0,
    parameter bit CDR_BYPASS      = 1'b0,
    parameter int ALIGN_MODE      = 0,
    parameter int SYNC_LEN        = 8,
    parameter logic [SYNC_LEN-1:0] SYNC_PATTERN = 'hBC,
    parameter int ADAPT_FRAC_BITS = 8,
    parameter int ADAPT_LOOP_GAIN = 8
) (
    // TX domain
    input  logic                              tx_clk,
    input  logic                              tx_rst_n,
    input  logic                              tx_enable,
    input  logic                              tx_load,
    input  logic [WORDS-1:0][DATA_WIDTH-1:0]  tx_par_data,
    output logic                              tx_ready,
    output logic                              tx_serial_out,
    output logic                              tx_serial_valid,
    output logic                              tx_busy,
    output logic                              tx_frame_done,

    // RX domain
    input  logic                              rx_clk,
    input  logic                              rx_rst_n,
    input  logic                              rx_enable,
    input  logic                              rx_flush,
    input  logic                              rx_align,
    input  logic                              rx_serial_in,
    output logic [WORDS-1:0][DATA_WIDTH-1:0]  rx_par_data,
    output logic                              rx_par_valid,
    output logic                              rx_busy,
    output logic                              rx_lock,
    output logic                              rx_lock_stale,
    output logic                              rx_sample_strobe,
    output logic                              rx_clk_out,
    output logic                              rx_frame_locked,
    output logic                              rx_frame_valid,
    output logic                              rx_serial_data,
    output logic                              rx_serial_valid,
    output logic signed [ADAPT_FRAC_BITS:0]   rx_freq_trim
);

    serdes_tx_digital #(
        .DATA_WIDTH(DATA_WIDTH),
        .WORDS(WORDS),
        .LSB_FIRST(LSB_FIRST),
        .CAPTURE_MODE(CAPTURE_MODE)
    ) u_tx (
        .tx_clk(tx_clk),
        .tx_rst_n(tx_rst_n),
        .tx_enable(tx_enable),
        .tx_load(tx_load),
        .tx_par_data(tx_par_data),
        .tx_ready(tx_ready),
        .tx_serial_out(tx_serial_out),
        .tx_serial_valid(tx_serial_valid),
        .tx_busy(tx_busy),
        .tx_frame_done(tx_frame_done)
    );

    serdes_rx_digital #(
        .DATA_WIDTH(DATA_WIDTH),
        .WORDS(WORDS),
        .LSB_FIRST(LSB_FIRST),
        .OVERSAMPLE(OVERSAMPLE),
        .SYNC_STAGES(SYNC_STAGES),
        .LOCK_THRESH(LOCK_THRESH),
        .LOCK_STALE_BITS(LOCK_STALE_BITS),
        .CDR_MODE(CDR_MODE),
        .CDR_BYPASS(CDR_BYPASS),
        .ALIGN_MODE(ALIGN_MODE),
        .SYNC_LEN(SYNC_LEN),
        .SYNC_PATTERN(SYNC_PATTERN),
        .ADAPT_FRAC_BITS(ADAPT_FRAC_BITS),
        .ADAPT_LOOP_GAIN(ADAPT_LOOP_GAIN)
    ) u_rx (
        .rx_clk(rx_clk),
        .rx_rst_n(rx_rst_n),
        .rx_enable(rx_enable),
        .rx_flush(rx_flush),
        .rx_align(rx_align),
        .rx_serial_in(rx_serial_in),
        .rx_par_data(rx_par_data),
        .rx_par_valid(rx_par_valid),
        .rx_busy(rx_busy),
        .rx_lock(rx_lock),
        .rx_lock_stale(rx_lock_stale),
        .rx_sample_strobe(rx_sample_strobe),
        .rx_clk_out(rx_clk_out),
        .rx_frame_locked(rx_frame_locked),
        .rx_frame_valid(rx_frame_valid),
        .rx_serial_data(rx_serial_data),
        .rx_serial_valid(rx_serial_valid),
        .rx_freq_trim(rx_freq_trim)
    );

endmodule
