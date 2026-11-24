`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// Raw PCS endpoint (preset: raw_pcs).
//
// Exposes a flat DATA_WIDTH-bit word bus plus a word-rate clock, the way an
// FPGA GT Raw PCS does, instead of the WORDS x DATA_WIDTH frame handshake of
// serdes_digital_top.
//
//   tx_usr_clk = clk / DATA_WIDTH   (word clock for the MAC side)
//   rx_rec_clk = clk / DATA_WIDTH   (recovered word clock)
//   tx_data/tx_valid/tx_ready       one word per tx_usr_clk cycle, gapless
//   rx_data/rx_valid                one word per rx_rec_clk cycle
//
// The whole design stays in the clk domain.  tx_usr_clk/rx_rec_clk are divided
// outputs, so the MAC side sees a word clock but no CDC is introduced; the
// generated SDC declares both with create_generated_clock -divide_by.
//
// Bitslip: rx_slide is a one-cycle pulse in the clk domain.  It resets the
// deserializer bit boundary, which slips the frame by one bit.  rx_align_bypass
// masks the internal comma detector, so an external PCS state machine can own
// alignment (JESD204 /K/, Ethernet sync headers, private protocols).
//
// This is a digital PCS, not a PMA: no 8b10b, no scrambler, no analog sampler.
// -----------------------------------------------------------------------------
module serdes_raw_pcs_top #(
    parameter int DATA_WIDTH  = 32,
    parameter bit LSB_FIRST   = 1'b1,
    parameter int OVERSAMPLE  = 1,
    parameter int CDR_MODE    = 2,
    parameter int SYNC_LEN    = 8,
    parameter logic [SYNC_LEN-1:0] SYNC_PATTERN = 'hBC,
    parameter int SYNC_STAGES = 3,
    parameter int PHASE_CTRL_W = 1
) (
    input  logic                              clk,
    input  logic                              rst_n,

    // TX word interface
    input  logic [DATA_WIDTH-1:0]             tx_data,
    input  logic                              tx_valid,
    output logic                              tx_ready,
    output logic                              tx_serial_out,
    output logic                              tx_serial_valid,
    output logic                              tx_usr_clk,

    // RX word interface
    input  logic                              rx_serial_in,
    output logic [DATA_WIDTH-1:0]             rx_data,
    output logic                              rx_valid,
    output logic                              rx_rec_clk,
    input  logic                              rx_slide,
    input  logic                              rx_align_bypass,
    output logic                              rx_lock,
    output logic                              rx_busy,
    output logic                              rx_frame_locked,
    output logic                              rx_clk_out
);

    localparam int CNT_W = (DATA_WIDTH <= 2) ? 1 : $clog2(DATA_WIDTH);

    // -------------------------------------------------------------------------
    // Word clock: registered divide-by-DATA_WIDTH with 50% duty.  DATA_WIDTH is
    // restricted to even values by the generator (16/20/32/40/64).
    // -------------------------------------------------------------------------
    logic [CNT_W-1:0] div_q;
    logic [CNT_W-1:0] div_next;
    logic             word_clk_q;

    assign div_next = (div_q == CNT_W'(DATA_WIDTH-1)) ? '0 : div_q + 1'b1;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            div_q      <= '0;
            word_clk_q <= 1'b0;
        end else begin
            div_q      <= div_next;
            word_clk_q <= (div_next < CNT_W'(DATA_WIDTH/2));
        end
    end

    assign tx_usr_clk = word_clk_q;
    assign rx_rec_clk = word_clk_q;

    // -------------------------------------------------------------------------
    // TX: one word per handshake, gapless in WORDS=1 mode.
    // -------------------------------------------------------------------------
    serializer_stream #(
        .DATA_WIDTH(DATA_WIDTH),
        .WORDS(1),
        .LSB_FIRST(LSB_FIRST)
    ) u_tx (
        .clk(clk),
        .rst_n(rst_n),
        .enable(1'b1),
        .in_valid(tx_valid),
        .in_ready(tx_ready),
        .in_word(tx_data),
        .serial_out(tx_serial_out),
        .serial_valid(tx_serial_valid),
        /* verilator lint_off PINCONNECTEMPTY */
        // TX status pulses: this wrapper exposes no TX status port;
        // intentionally unused.
        .busy(),
        .frame_done()
        /* verilator lint_on PINCONNECTEMPTY */
    );

    // -------------------------------------------------------------------------
    // RX: CDR/sampler + deserializer (WORDS=1) with external alignment.
    // serdes_rx_digital is built with ALIGN_MODE=0 (no internal comma), and
    // this wrapper owns the comma detector so it can be bypassed at runtime.
    // -------------------------------------------------------------------------
    logic [0:0][DATA_WIDTH-1:0] rx_par_data;
    // Link-health watchdog outputs from the RX core: this wrapper exposes no
    // link-health port, so they are intentionally left unused.
    /* verilator lint_off UNUSEDSIGNAL */
    logic comma_lock_timeout_unused, comma_lock_stuck_unused;
    /* verilator lint_on UNUSEDSIGNAL */
    logic                       rx_par_valid;
    logic                       rx_serial_data;
    logic                       rx_serial_valid;
    logic                       comma_align_pulse;
    logic                       comma_locked;
    logic                       rx_align;

    serdes_rx_digital #(
        .DATA_WIDTH(DATA_WIDTH),
        .WORDS(1),
        .LSB_FIRST(LSB_FIRST),
        .OVERSAMPLE(OVERSAMPLE),
        .CDR_MODE(CDR_MODE),
        .CDR_BYPASS(OVERSAMPLE == 1),
        .ALIGN_MODE(0),
        .SYNC_LEN(SYNC_LEN),
        .SYNC_PATTERN(SYNC_PATTERN),
        .SYNC_STAGES(SYNC_STAGES),
        .PHASE_CTRL_W(PHASE_CTRL_W)
    ) u_rx (
        .rx_clk(clk),
        .rx_rst_n(rst_n),
        .rx_enable(1'b1),
        .rx_flush(1'b0),
        .rx_align(rx_align),
        .rx_serial_in(rx_serial_in),
        .phase_offset('0),
        .rx_smp(1'b0),
        /* verilator lint_off PINCONNECTEMPTY */
        // rx_dll_tap is unused: this wrapper has no adaptive-CDR control path.
        .rx_dll_tap(),
        /* verilator lint_on PINCONNECTEMPTY */
        .rx_par_data(rx_par_data),
        .rx_par_valid(rx_par_valid),
        .rx_busy(rx_busy),
        .rx_lock(rx_lock),
        /* verilator lint_off PINCONNECTEMPTY */
        // rx_lock_stale / rx_sample_strobe / rx_freq_trim and the link-health
        // watchdog outputs are status only; this wrapper exposes none of them.
        // comma_lock_* are intentionally left unused (see serdes_digital_top).
        .rx_lock_stale(),
        .rx_sample_strobe(),
        /* verilator lint_on PINCONNECTEMPTY */
        .rx_clk_out(rx_clk_out),
        /* verilator lint_off PINCONNECTEMPTY */
        .rx_frame_locked(),
        .rx_frame_valid(),
        /* verilator lint_on PINCONNECTEMPTY */
        .rx_serial_data(rx_serial_data),
        .rx_serial_valid(rx_serial_valid),
        /* verilator lint_off PINCONNECTEMPTY */
        .rx_freq_trim(),
        /* verilator lint_on PINCONNECTEMPTY */
        .comma_lock_timeout(comma_lock_timeout_unused),
        .comma_lock_stuck(comma_lock_stuck_unused)
    );

    comma_align #(
        .PATTERN_LEN(SYNC_LEN),
        .PATTERN(SYNC_PATTERN),
        .LSB_FIRST(LSB_FIRST)
    ) u_comma (
        .clk(clk),
        .rst_n(rst_n),
        .enable(1'b1),
        .flush(1'b0),
        .rearm(rx_par_valid),
        .serial_in(rx_serial_data),
        .serial_valid(rx_serial_valid),
        .align(comma_align_pulse),
        .locked(comma_locked)
    );

    assign rx_align = rx_slide | (comma_align_pulse & ~rx_align_bypass);

    assign rx_data         = rx_par_data[0];
    assign rx_valid        = rx_par_valid;
    assign rx_frame_locked = comma_locked | rx_align_bypass;

endmodule
