`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// Digital top level assembled from independently synthesizable TX and RX
// endpoint wrappers.  The same RTL can be instantiated with different PDK
// standard-cell libraries for TX and RX; only the wrappers and cell mappings
// change, not the protocol.
//
//   tx_clk : bit clock for the serializer.
//   rx_clk : oversampling clock for the CDR; frequency = OVERSAMPLE * data rate.
//
// The CDR produces data_out/sample_strobe in the rx_clk domain.  The
// deserializer consumes sample_strobe; rx_lock gates capture until the CDR has
// acquired enough edges.
// -----------------------------------------------------------------------------
module serdes_digital_top #(
    parameter int DATA_WIDTH   = 32,
    parameter int WORDS        = 8,
    parameter bit LSB_FIRST    = 1'b1,
    parameter bit CAPTURE_MODE = 1'b0,
    parameter int OVERSAMPLE   = 4,
    parameter int SYNC_STAGES  = 2,
    parameter int LOCK_THRESH  = 8,
    parameter int LOCK_RELEASE = 4,
    parameter bit CDR_BYPASS   = 1'b0
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
    output logic                              rx_sample_strobe,
    output logic                              rx_clk_out
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
        .LOCK_RELEASE(LOCK_RELEASE),
        .CDR_BYPASS(CDR_BYPASS)
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
        .rx_sample_strobe(rx_sample_strobe),
        .rx_clk_out(rx_clk_out)
    );

endmodule
