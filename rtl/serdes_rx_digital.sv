`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// Digital RX endpoint.
//
// CDR modes (CDR_MODE, or the legacy CDR_BYPASS alias):
//   0 / fixed      : fixed-rate oversampling CDR (source-locked or low-ppm)
//   1 / adaptive   : frequency-tracking oversampling CDR
//   2 / source_sync: external rx_clk is the bit clock; receiver_sampler only
//
// ALIGN_MODE:
//   0: external rx_align input controls the deserializer bit boundary.
//   1: comma_align searches SYNC_PATTERN once and asserts the deserializer
//      align at the pattern boundary.
//   2: comma_align re-arms after every rx_par_valid, so each frame can
//      re-align on its own sync word.  Use this for continuous/gapped links.
//
// rx_frame_locked is sticky after a comma match (ALIGN_MODE=1).  It is cleared
// by rx_flush or loss of CDR lock.  In ALIGN_MODE=0 it is tied high because the
// external align contract is owned by the integrator.
//
// rx_serial_data / rx_serial_valid expose the recovered serial stream for
// scoreboards and link monitoring.  They are the same signals consumed by the
// deserializer, not a separate debug path.
// -----------------------------------------------------------------------------
module serdes_rx_digital #(
    parameter int DATA_WIDTH      = 32,
    parameter int WORDS           = 8,
    parameter bit LSB_FIRST       = 1'b1,
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
    parameter int ADAPT_LOOP_GAIN = 8
) (
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

    localparam int MODE = (CDR_BYPASS || (CDR_MODE == 2)) ? 2 :
                          ((CDR_MODE == 1) ? 1 : 0);

    logic rx_rst_n_sync;
    logic cdr_data_out;
    logic cdr_sample_strobe;
    logic cdr_lock;
    logic cdr_lock_stale;
    logic cdr_clk_out;
    logic comma_align_pulse;
    logic comma_locked;
    logic des_align;
    logic signed [ADAPT_FRAC_BITS:0] cdr_freq_trim;

    reset_sync #(.STAGES(2)) u_reset_sync (
        .clk(rx_clk),
        .rst_n_in(rx_rst_n),
        .rst_n_out(rx_rst_n_sync)
    );

    generate
        if (MODE == 2) begin : g_bypass
            logic bypass_valid_q;

            // Receiver-only mode: external rx_clk is the bit clock.  The
            // receiver_sampler models only the digital q <= d boundary.
            receiver_sampler u_sampler (
                .clk(rx_clk),
                .rst_n(rx_rst_n_sync),
                .enable(rx_enable),
                .d(rx_serial_in),
                .q(cdr_data_out)
            );

            always_ff @(posedge rx_clk or negedge rx_rst_n_sync) begin
                if (!rx_rst_n_sync)
                    bypass_valid_q <= 1'b0;
                else
                    bypass_valid_q <= rx_enable;
            end

            assign cdr_sample_strobe = bypass_valid_q;
            assign cdr_lock          = 1'b1;
            assign cdr_lock_stale    = 1'b0;
            assign cdr_clk_out       = rx_clk;
            assign cdr_freq_trim     = '0;
        end else if (MODE == 1) begin : g_adaptive
            cdr_adaptive #(
                .OVERSAMPLE(OVERSAMPLE),
                .SYNC_STAGES(SYNC_STAGES),
                .FRAC_BITS(ADAPT_FRAC_BITS),
                .LOOP_GAIN(ADAPT_LOOP_GAIN),
                .LOCK_THRESH(LOCK_THRESH),
                .LOCK_STALE_BITS(LOCK_STALE_BITS)
            ) u_cdr (
                .clk(rx_clk),
                .rst_n(rx_rst_n_sync),
                .enable(rx_enable),
                .flush(rx_flush),
                .data_in(rx_serial_in),
                .data_out(cdr_data_out),
                .sample_strobe(cdr_sample_strobe),
                .clk_out(cdr_clk_out),
                .lock(cdr_lock),
                .lock_stale(cdr_lock_stale),
                .freq_trim(cdr_freq_trim)
            );
        end else begin : g_fixed
            cdr_oversampling #(
                .OVERSAMPLE(OVERSAMPLE),
                .SYNC_STAGES(SYNC_STAGES),
                .LOCK_THRESH(LOCK_THRESH),
                .LOCK_STALE_BITS(LOCK_STALE_BITS)
            ) u_cdr (
                .clk(rx_clk),
                .rst_n(rx_rst_n_sync),
                .enable(rx_enable),
                .flush(rx_flush),
                .data_in(rx_serial_in),
                .data_out(cdr_data_out),
                .sample_strobe(cdr_sample_strobe),
                .clk_out(cdr_clk_out),
                .lock(cdr_lock),
                .lock_stale(cdr_lock_stale)
            );
            assign cdr_freq_trim = '0;
        end
    endgenerate

    generate
        if (ALIGN_MODE != 0) begin : g_comma
            comma_align #(
                .PATTERN_LEN(SYNC_LEN),
                .PATTERN(SYNC_PATTERN),
                .LSB_FIRST(LSB_FIRST)
            ) u_comma_align (
                .clk(rx_clk),
                .rst_n(rx_rst_n_sync),
                .enable(rx_enable),
                .flush(rx_flush | ~cdr_lock),
                .rearm((ALIGN_MODE == 2) ? rx_par_valid : 1'b0),
                .serial_in(cdr_data_out),
                .serial_valid(cdr_sample_strobe & cdr_lock),
                .align(comma_align_pulse),
                .locked(comma_locked)
            );
        end else begin : g_external_align
            assign comma_align_pulse = 1'b0;
            assign comma_locked      = 1'b1;
        end
    endgenerate

    assign des_align = rx_align | comma_align_pulse;

    deserializer #(
        .DATA_WIDTH(DATA_WIDTH),
        .WORDS(WORDS),
        .LSB_FIRST(LSB_FIRST)
    ) u_deserializer (
        .clk(rx_clk),
        .rst_n(rx_rst_n_sync),
        .enable(rx_enable),
        .align(des_align),
        .flush(rx_flush),
        .serial_in(cdr_data_out),
        .serial_valid(cdr_sample_strobe & cdr_lock),
        .par_data(rx_par_data),
        .par_valid(rx_par_valid),
        .busy(rx_busy)
    );

    assign rx_lock          = cdr_lock;
    assign rx_lock_stale    = cdr_lock_stale;
    assign rx_sample_strobe = cdr_sample_strobe;
    assign rx_clk_out       = cdr_clk_out;
    assign rx_frame_locked  = (ALIGN_MODE != 0) ? comma_locked : 1'b1;
    assign rx_frame_valid   = rx_par_valid & rx_frame_locked;
    assign rx_serial_data   = cdr_data_out;
    assign rx_serial_valid  = cdr_sample_strobe & cdr_lock;
    assign rx_freq_trim     = cdr_freq_trim;

endmodule
