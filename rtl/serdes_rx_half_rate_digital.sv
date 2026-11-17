`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// Half-rate digital RX endpoint.
//
// The front sampler runs at the bit rate (clk_bit) and the wide parallel bank
// runs at clk_word = clk_bit / 2, so the DATA_WIDTH x WORDS registers never
// toggle at the line rate.  Two consecutive sampled bits are grouped into one
// pair by rx_pair_align and consumed by deserializer_half_rate.
//
// Rate mode scope (first integration): source-synchronous / fixed-phase only.
// clk_bit is the bit clock (CDR_MODE=2 receiver_sampler, or an external
// source-locked clock).  The oversampling and bang-bang CDRs run on an
// OS x bit-rate sample clock and need a divided sample-clock contract for the
// parallel bank; that is a separate follow-up (see docs/half_rate.md).
//
// Frame alignment: comma_align watches the recovered bit stream at clk_bit.
// Its align pulse drops the pattern's last bit and restarts the pair phase, so
// the next pair is word0 bits 0-1.  rx_align is the external align input.
//
// Clock contract: clk_word must be the divide-by-2 clock of clk_bit from the
// same source.  The generated SDC enforces it with create_generated_clock.
// -----------------------------------------------------------------------------
module serdes_rx_half_rate_digital #(
    parameter int DATA_WIDTH  = 32,
    parameter int WORDS       = 8,
    parameter bit LSB_FIRST   = 1'b1,
    parameter int SYNC_STAGES = 3,
    parameter int ALIGN_MODE  = 0,
    parameter int SYNC_LEN    = 8,
    parameter logic [SYNC_LEN-1:0] SYNC_PATTERN = 'hBC,
    parameter int PHASE_CTRL_W = 1
) (
    input  logic                              clk_bit,
    input  logic                              clk_word,
    input  logic                              rst_n,
    input  logic                              rx_enable,
    input  logic                              rx_flush,
    input  logic                              rx_align,
    input  logic                              rx_serial_in,
    /* verilator lint_off UNUSEDSIGNAL */
    input  logic [PHASE_CTRL_W-1:0]           phase_offset,
    /* verilator lint_on UNUSEDSIGNAL */
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
    output logic [11:0]                       rx_freq_trim
);

    logic rx_rst_n_sync;
    logic cdr_data_out;
    logic cdr_sample_strobe;
    logic comma_align_pulse;
    logic comma_locked;
    logic pair_valid;
    logic pair_align;
    logic pair_bit0;
    logic pair_bit1;

    reset_sync #(.STAGES(2)) u_reset_sync (
        .clk(clk_bit),
        .rst_n_in(rst_n),
        .rst_n_out(rx_rst_n_sync)
    );

    // Source-synchronous front sampler: clk_bit is the bit clock.
    receiver_sampler u_sampler (
        .clk(clk_bit),
        .rst_n(rx_rst_n_sync),
        .enable(rx_enable),
        .d(rx_serial_in),
        .q(cdr_data_out)
    );

    always_ff @(posedge clk_bit or negedge rx_rst_n_sync) begin
        if (!rx_rst_n_sync)
            cdr_sample_strobe <= 1'b0;
        else
            cdr_sample_strobe <= rx_enable;
    end

    generate
        if (ALIGN_MODE == 1 || ALIGN_MODE == 2) begin : g_comma
            comma_align #(
                .PATTERN_LEN(SYNC_LEN),
                .PATTERN(SYNC_PATTERN),
                .LSB_FIRST(LSB_FIRST)
            ) u_comma_align (
                .clk(clk_bit),
                .rst_n(rx_rst_n_sync),
                .enable(rx_enable),
                .flush(rx_flush),
                .rearm((ALIGN_MODE == 2) ? rx_par_valid : 1'b0),
                .serial_in(cdr_data_out),
                .serial_valid(cdr_sample_strobe),
                .align(comma_align_pulse),
                .locked(comma_locked)
            );
        end else begin : g_external_align
            assign comma_align_pulse = 1'b0;
            assign comma_locked      = 1'b1;
        end
    endgenerate

    rx_pair_align u_pair (
        .clk_bit(clk_bit),
        .clk_word(clk_word),
        .rst_n(rx_rst_n_sync),
        .enable(rx_enable),
        .align(rx_align | comma_align_pulse),
        .bit_in(cdr_data_out),
        .bit_valid(cdr_sample_strobe),
        .pair_valid(pair_valid),
        .pair_align(pair_align),
        .pair_bit0(pair_bit0),
        .pair_bit1(pair_bit1)
    );

    deserializer_half_rate #(
        .DATA_WIDTH(DATA_WIDTH),
        .WORDS(WORDS),
        .LSB_FIRST(LSB_FIRST)
    ) u_deserializer (
        .clk_word(clk_word),
        .rst_n(rx_rst_n_sync),
        .enable(rx_enable),
        .clear(rx_flush),
        .align(pair_align),
        .pair_valid(pair_valid),
        .bit0(pair_bit0),
        .bit1(pair_bit1),
        .par_data(rx_par_data),
        .par_valid(rx_par_valid),
        .busy(rx_busy)
    );

    assign rx_lock          = 1'b1;
    assign rx_lock_stale    = 1'b0;
    assign rx_sample_strobe = cdr_sample_strobe;
    assign rx_clk_out       = clk_bit;
    assign rx_frame_locked  = (ALIGN_MODE == 0) ? 1'b1 : comma_locked;
    assign rx_frame_valid   = rx_par_valid & rx_frame_locked;
    assign rx_serial_data   = cdr_data_out;
    assign rx_serial_valid  = cdr_sample_strobe;
    assign rx_freq_trim     = '0;

endmodule
