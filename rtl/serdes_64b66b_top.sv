`timescale 1ns/1ps

// 64b/66b link wrapper.
//
// Payload words are 64 bits and are encoded into 66-bit PCS blocks before
// serialization; the RX core deserializes 66-bit blocks and the decoder
// reconstructs 64-bit payload words.  The current PCS primitive covers data
// and control-header blocks plus a 66-to-64 gearbox; it does not implement
// full IEEE control-field/lane-distribution semantics.
module serdes_64b66b_top #(
    parameter int DATA_WIDTH      = 64,
    parameter int WORDS           = 8,
    parameter bit LSB_FIRST       = 1'b1,
    parameter bit CAPTURE_MODE    = 1'b0,
    parameter int OVERSAMPLE      = 1,
    parameter int SYNC_STAGES     = 3,
    parameter int LOCK_THRESH     = 8,
    parameter int LOCK_STALE_BITS = 16,
    parameter int CDR_MODE        = 2,
    parameter bit CDR_BYPASS      = 1'b1,
    parameter int ALIGN_MODE      = 0,
    parameter int SYNC_LEN        = 8,
    parameter logic [SYNC_LEN-1:0] SYNC_PATTERN = 'hBC,
    parameter int ADAPT_FRAC_BITS = 12,
    parameter int ADAPT_LOOP_GAIN = 8
) (
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
    output logic                              sync_error
);
    localparam int LINE_WIDTH = 66;

    logic [WORDS-1:0][LINE_WIDTH-1:0] tx_line;
    logic [WORDS-1:0][LINE_WIDTH-1:0] rx_line;
    logic [WORDS-1:0] tx_sync_ok;
    logic [WORDS-1:0] rx_sync_ok;
    /* verilator lint_off UNUSEDSIGNAL */
    logic [WORDS-1:0] rx_ctrl;
    /* verilator lint_on UNUSEDSIGNAL */

    generate
        for (genvar w = 0; w < WORDS; w++) begin : g_tx_enc
            // Scrambler state persists per lane; advance on each new payload
            // (tx_load) / each received block (rx_par_valid).
            pcs_64b66b_encoder u_enc (
                .clk(tx_clk), .rst_n(tx_rst_n), .advance(tx_load),
                .data_in(tx_par_data[w]), .ctrl(1'b0),
                .block_out(tx_line[w]), .sync_ok(tx_sync_ok[w])
            );
            pcs_64b66b_decoder u_dec (
                .clk(rx_clk), .rst_n(rx_rst_n), .advance(rx_par_valid),
                .block_in(rx_line[w]), .data_out(rx_par_data[w]),
                .sync_ok(rx_sync_ok[w]), .ctrl_block(rx_ctrl[w])
            );
        end
    endgenerate

    assign sync_error = ~(&rx_sync_ok) | ~(&tx_sync_ok);

    serdes_tx_digital #(
        .DATA_WIDTH(LINE_WIDTH), .WORDS(WORDS),
        .LSB_FIRST(LSB_FIRST), .CAPTURE_MODE(CAPTURE_MODE)
    ) u_tx (
        .tx_clk(tx_clk), .tx_rst_n(tx_rst_n), .tx_enable(tx_enable),
        .tx_load(tx_load), .tx_par_data(tx_line), .tx_ready(tx_ready),
        .tx_serial_out(tx_serial_out), .tx_serial_valid(tx_serial_valid),
        .tx_busy(tx_busy), .tx_frame_done(tx_frame_done)
    );

    serdes_rx_digital #(
        .DATA_WIDTH(LINE_WIDTH), .WORDS(WORDS), .LSB_FIRST(LSB_FIRST),
        .OVERSAMPLE(OVERSAMPLE), .SYNC_STAGES(SYNC_STAGES),
        .LOCK_THRESH(LOCK_THRESH), .LOCK_STALE_BITS(LOCK_STALE_BITS),
        .CDR_MODE(CDR_MODE), .CDR_BYPASS(CDR_BYPASS), .ALIGN_MODE(ALIGN_MODE),
        .SYNC_LEN(SYNC_LEN), .SYNC_PATTERN(SYNC_PATTERN),
        .ADAPT_FRAC_BITS(ADAPT_FRAC_BITS), .ADAPT_LOOP_GAIN(ADAPT_LOOP_GAIN)
    ) u_rx (
        .rx_clk(rx_clk), .rx_rst_n(rx_rst_n), .rx_enable(rx_enable),
        .rx_flush(rx_flush), .rx_align(rx_align), .rx_serial_in(rx_serial_in),
        .phase_offset('0),
        .rx_par_data(rx_line), .rx_par_valid(rx_par_valid),
        .rx_busy(rx_busy), .rx_lock(rx_lock), .rx_lock_stale(rx_lock_stale),
        .rx_sample_strobe(rx_sample_strobe), .rx_clk_out(rx_clk_out),
        .rx_frame_locked(rx_frame_locked), .rx_frame_valid(rx_frame_valid),
        .rx_serial_data(rx_serial_data), .rx_serial_valid(rx_serial_valid),
        .rx_freq_trim(rx_freq_trim)
    );
endmodule
