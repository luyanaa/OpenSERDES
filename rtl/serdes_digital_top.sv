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
/* verilator lint_off UNUSEDSIGNAL */
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
    parameter int ADAPT_FRAC_BITS = 12,
    parameter int ADAPT_LOOP_GAIN = 8,
    parameter int PHASE_CTRL_W = (OVERSAMPLE <= 1) ? 1 : $clog2(OVERSAMPLE),
    parameter bit TRAINING_ENABLE = 1'b0,
    parameter int TRAIN_DWELL_CYCLES = 64,
    parameter int TRAIN_ERROR_WIDTH = 16
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
    output logic signed [ADAPT_FRAC_BITS:0]   rx_freq_trim,

    // Optional digital training interface.  The error count is supplied by
    // the link scoreboard/PRBS checker; the phase request drives the CDR's
    // external calibration input.  TRAINING_ENABLE defaults to 0 so existing
    // generated packages and testbenches are unchanged.
    input  logic                              train_start,
    input  logic [TRAIN_ERROR_WIDTH-1:0]      train_error_count,
    output logic                              train_locked,
    output logic                              train_busy,
    output logic                              train_retrain,
    output logic                              train_phase_we,
    output logic                              train_thresh_we,
    output logic [PHASE_CTRL_W-1:0]           train_phase,
    output logic [PHASE_CTRL_W-1:0]           train_best_phase,
    output logic [TRAIN_ERROR_WIDTH-1:0]      train_best_errors
);
/* verilator lint_on UNUSEDSIGNAL */

    logic [PHASE_CTRL_W-1:0] phase_offset;

    generate
        if (TRAINING_ENABLE) begin : g_training
            /* verilator lint_off PINCONNECTEMPTY */
            link_training_fsm #(
                .PHASE_STEPS(OVERSAMPLE),
                .THRESH_STEPS(1),
                .DWELL_CYCLES(TRAIN_DWELL_CYCLES),
                .ERROR_WIDTH(TRAIN_ERROR_WIDTH),
                .RETRAIN_ERR_THRESH(4),
                .RETRAIN_WINDOW(256)
            ) u_train (
                .clk(rx_clk),
                .rst_n(rx_rst_n),
                .enable(rx_enable),
                .start(train_start),
                .error_count(train_error_count),
                .phase_sel(phase_offset),
                .thresh_sel(),
                .phase_we(train_phase_we),
                .thresh_we(train_thresh_we),
                .busy(train_busy),
                .locked(train_locked),
                .retrain(train_retrain),
                .state(),
                .best_phase(train_best_phase),
                .best_thresh(),
                .best_errors(train_best_errors)
            );
            /* verilator lint_on PINCONNECTEMPTY */
            assign train_phase = phase_offset;
        end else begin : g_no_training
            assign phase_offset      = '0;
            assign train_locked      = 1'b0;
            assign train_busy        = 1'b0;
            assign train_retrain     = 1'b0;
            assign train_phase_we    = 1'b0;
            assign train_thresh_we   = 1'b0;
            assign train_phase       = '0;
            assign train_best_phase  = '0;
            assign train_best_errors = '0;
        end
    endgenerate

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
        .phase_offset(phase_offset),
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
