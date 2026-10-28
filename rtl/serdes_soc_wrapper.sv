`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// Single-clock SoC reference wrapper.
//
//   APB CSR + TX AXIS endpoint + source-synchronous RX AXIS endpoint.
//
// The link protocol uses one comma word at the start of each TX frame:
//   TX AXIS frame = WORDS+1 beats: comma (0xBC) + WORDS payload words
//   RX AXIS frame = WORDS payload beats
//
// This wrapper is intentionally single-clock: APB, AXIS, TX and RX share clk.
// A multi-clock CDC wrapper (separate s_axis/m_axis/pclk/tx/rx clocks) is the
// next integration step; use serdes_tx_axis_digital / serdes_rx_axis_digital
// plus async_fifo for that.
//
// APB map (word aligned):
//   0: CONTROL  [0] tx_enable, [1] rx_enable, [2] rx_flush,
//               [3] tx_clear, [4] rx_clear
//   1..6: scratch
//   7: STATUS   [0] tx_axis_ready, [1] tx_busy, [2] tx_frame_done,
//               [3] tx_frame_error, [4] rx_lock, [5] rx_lock_stale,
//               [6] rx_frame_locked, [7] rx_frame_overflow,
//               [8] rx_par_valid, [9] rx_busy
//   8: BIST_CTRL  [0] enable, [1] start, [2] clear, [3] inject,
//                 [4] loopback, [5] snapshot request
//   9: reserved (RW)
//  10: BIST_ERROR_COUNT (RO, snapshot)
//  11: BIST_BIT_COUNT   (RO, snapshot)
//  12: BIST_STATUS      [0] error (snapshot), [1] running (snapshot = start&enable),
//                       [2] snapshot valid (live), [3] loopback (live CSR),
//                       [4] inject (live CSR), [7:5] PRBS width code
//                       3'b001 = 7, 3'b010 = 15, 3'b011 = 31
//  13..15: reserved (RW)
//
// BIST mode (BIST_CTRL[0]) gates the TX/RX serializer cores off and drives
// tx_serial_out from the PRBS generator.  Loopback (BIST_CTRL[4]) also feeds
// the BIST checker and, in normal mode, the RX core from tx_serial_out.
// -----------------------------------------------------------------------------
module serdes_soc_wrapper #(
    parameter int DATA_WIDTH = 8,
    parameter int WORDS      = 4,
    parameter bit LSB_FIRST  = 1'b1,
    parameter int SYNC_LEN   = 8,
    parameter logic [SYNC_LEN-1:0] SYNC_PATTERN = 'hBC,
    parameter int ADAPT_FRAC_BITS = 12,
    parameter int BIST_PRBS_WIDTH = 7,
    parameter logic [BIST_PRBS_WIDTH-1:0] BIST_SEED = '1
) (
    input  logic                              clk,
    input  logic                              rst_n,

    input  logic [15:0]                       paddr,
    input  logic                              psel,
    input  logic                              penable,
    input  logic                              pwrite,
    input  logic [31:0]                       pwdata,
    output logic                              pready,
    output logic [31:0]                       prdata,
    output logic                              pslverr,

    input  logic                              s_axis_tvalid,
    output logic                              s_axis_tready,
    input  logic [DATA_WIDTH-1:0]             s_axis_tdata,
    input  logic                              s_axis_tlast,

    output logic                              m_axis_tvalid,
    input  logic                              m_axis_tready,
    output logic [DATA_WIDTH-1:0]             m_axis_tdata,
    output logic                              m_axis_tlast,

    output logic                              tx_serial_out,
    output logic                              tx_serial_valid,
    input  logic                              rx_serial_in,
    output logic                              rx_sample_strobe,
    output logic                              rx_serial_data,
    output logic                              rx_serial_valid,
    output logic signed [ADAPT_FRAC_BITS:0]   rx_freq_trim,
    output logic                              rx_clk_out,
    output logic [15:0][31:0]                 csr_regs
);

    localparam int TX_WORDS = WORDS + 1;
    localparam int RX_WORDS = WORDS;
    localparam logic [DATA_WIDTH-1:0] BIST_COMMA = DATA_WIDTH'(SYNC_PATTERN);

    localparam logic [2:0] BIST_WIDTH_CODE =
        (BIST_PRBS_WIDTH == 7)  ? 3'b001 :
        (BIST_PRBS_WIDTH == 15) ? 3'b010 :
        (BIST_PRBS_WIDTH == 31) ? 3'b011 : 3'b000;

    logic [31:0]      status;

    logic tx_enable;
    logic rx_enable;
    logic rx_flush;
    logic tx_clear;
    logic rx_clear;

    logic tx_busy;
    logic tx_frame_done;
    logic tx_frame_error;

    logic rx_lock;
    logic rx_lock_stale;
    logic rx_frame_locked;
    logic rx_frame_overflow;
    logic rx_par_valid;
    logic rx_busy;

    // BIST / loopback control and status.
    logic        bist_enable;
    logic        bist_start;
    logic        bist_clear;
    logic        bist_inject;
    logic        bist_loopback;
    logic        bist_word_mode;
    logic        bist_pad_mode;
    logic        bist_snapshot_req;
    logic        bist_serial_out;
    logic        bist_check_in;
    logic        bist_error;
    logic [31:0] bist_error_count;
    logic [31:0] bist_bit_count;

    logic        bist_snap_valid;
    logic        bist_error_snap;
    logic        bist_running_snap;
    logic [31:0] bist_error_count_snap;
    logic [31:0] bist_bit_count_snap;

    // Word/datapath BIST (AXIS-level PRBS frame source and sink).
    logic        bist_word_valid;
    logic        bist_word_ready;
    logic        bist_word_last;
    logic [DATA_WIDTH-1:0] bist_word_data;
    logic        tx_axis_tvalid;
    logic        tx_axis_tready;
    logic [DATA_WIDTH-1:0] tx_axis_tdata;
    logic        tx_axis_tlast;
    logic        rx_axis_tvalid;
    logic        rx_axis_tready;
    logic [DATA_WIDTH-1:0] rx_axis_tdata;
    logic        rx_axis_tlast;
    logic        bist_word_error;
    logic [31:0] bist_word_error_count;
    logic [31:0] bist_word_frame_count;
    logic        bist_word_snap_valid;
    logic        bist_word_error_snap;
    logic        bist_word_running_snap;
    logic [31:0] bist_word_error_count_snap;
    logic [31:0] bist_word_frame_count_snap;
    logic        bist_snap_valid_all;

    // Core-side serial signals, muxed with the BIST datapath.
    logic        tx_core_serial_out;
    logic        tx_core_serial_valid;
    logic        rx_core_serial_in;
    logic        tx_core_enable;
    logic        rx_core_enable;

    logic [15:0][31:0] csr_ro;

    assign tx_enable = csr_regs[0][0];
    assign rx_enable = csr_regs[0][1];
    assign rx_flush  = csr_regs[0][2];
    assign tx_clear  = csr_regs[0][3];
    assign rx_clear  = csr_regs[0][4];

    assign bist_enable       = csr_regs[8][0];
    assign bist_start        = csr_regs[8][1];
    assign bist_clear        = csr_regs[8][2];
    assign bist_inject       = csr_regs[8][3];
    assign bist_loopback     = csr_regs[8][4];
    assign bist_snapshot_req = csr_regs[8][5];
    assign bist_word_mode    = csr_regs[8][6];
    assign bist_pad_mode     = bist_enable & ~bist_word_mode;

    assign status = {
        22'b0,
        rx_busy, rx_par_valid, rx_frame_overflow, rx_frame_locked,
        rx_lock_stale, rx_lock, tx_frame_error, tx_frame_done,
        tx_busy, s_axis_tready
    };

    // Pad-level BIST owns the serial line and parks the cores.  Word BIST
    // keeps the cores alive and injects/checks at the AXIS boundary instead.
    assign tx_core_enable = (tx_enable | bist_word_mode) & ~bist_pad_mode;
    assign rx_core_enable = (rx_enable | bist_word_mode) & ~bist_pad_mode;

    assign tx_serial_out   = bist_pad_mode ? bist_serial_out   : tx_core_serial_out;
    assign tx_serial_valid = bist_pad_mode ? bist_start        : tx_core_serial_valid;
    assign rx_core_serial_in = bist_loopback ? tx_serial_out : rx_serial_in;

    // Checker injection flips the monitored bit without touching the generator.
    // tx_serial_out does not depend on bist_check_in, so there is no loop.
    assign bist_check_in = (bist_loopback ? tx_serial_out : rx_serial_in) ^ bist_inject;

    always_comb begin
        csr_ro = '0;
        csr_ro[10] = bist_error_count_snap;
        csr_ro[11] = bist_bit_count_snap;
        csr_ro[12] = {21'b0, bist_word_running_snap, bist_word_error_snap,
                      bist_word_mode, BIST_WIDTH_CODE, bist_inject, bist_loopback,
                      bist_snap_valid_all, bist_running_snap, bist_error_snap};
        csr_ro[13] = bist_word_error_count_snap;
        csr_ro[14] = bist_word_frame_count_snap;
    end

    assign bist_snap_valid_all = bist_snap_valid & bist_word_snap_valid;

    apb_csr #(
        .NUM_REGS(16),
        .DATA_WIDTH(32),
        .ADDR_WIDTH(16),
        .STATUS_IDX(7),
        .RO_MASK(16'b0111_1100_0000_0000)
    ) u_csr (
        .pclk(clk),
        .presetn(rst_n),
        .paddr(paddr),
        .psel(psel),
        .penable(penable),
        .pwrite(pwrite),
        .pwdata(pwdata),
        .pready(pready),
        .prdata(prdata),
        .pslverr(pslverr),
        .regs_o(csr_regs),
        .status_i(status),
        .ro_i(csr_ro)
    );

    // -------------------------------------------------------------------------
    // Word/datapath BIST: AXIS-level PRBS frame source and sink.
    // -------------------------------------------------------------------------
    prbs_word_gen #(
        .WIDTH(BIST_PRBS_WIDTH),
        .DATA_WIDTH(DATA_WIDTH),
        .WORDS(WORDS),
        .SEED(BIST_SEED),
        .COMMA(BIST_COMMA)
    ) u_bist_word_gen (
        .clk(clk),
        .rst_n(rst_n),
        .start(bist_start & bist_word_mode),
        .clear(bist_clear),
        .in_valid(bist_word_valid),
        .in_ready(bist_word_ready),
        .in_word(bist_word_data),
        .in_last(bist_word_last)
    );

    assign tx_axis_tvalid = bist_word_mode ? bist_word_valid : s_axis_tvalid;
    assign tx_axis_tdata  = bist_word_mode ? bist_word_data  : s_axis_tdata;
    assign tx_axis_tlast  = bist_word_mode ? bist_word_last  : s_axis_tlast;
    assign s_axis_tready  = bist_word_mode ? 1'b0 : tx_axis_tready;
    assign bist_word_ready = bist_word_mode ? tx_axis_tready : 1'b0;

    serdes_tx_axis_digital #(
        .DATA_WIDTH(DATA_WIDTH),
        .WORDS(TX_WORDS),
        .LSB_FIRST(LSB_FIRST)
    ) u_tx (
        .tx_clk(clk),
        .tx_rst_n(rst_n),
        .tx_enable(tx_core_enable),
        .tx_clear(tx_clear),
        .s_axis_tvalid(tx_axis_tvalid),
        .s_axis_tready(tx_axis_tready),
        .s_axis_tdata(tx_axis_tdata),
        .s_axis_tlast(tx_axis_tlast),
        .tx_serial_out(tx_core_serial_out),
        .tx_serial_valid(tx_core_serial_valid),
        .tx_busy(tx_busy),
        .tx_frame_done(tx_frame_done),
        .tx_frame_error(tx_frame_error)
    );

    serdes_rx_axis_digital #(
        .DATA_WIDTH(DATA_WIDTH),
        .WORDS(RX_WORDS),
        .LSB_FIRST(LSB_FIRST),
        .OVERSAMPLE(1),
        .CDR_BYPASS(1'b1),
        .ALIGN_MODE(2),
        .SYNC_LEN(SYNC_LEN),
        .SYNC_PATTERN(SYNC_PATTERN),
        .ADAPT_FRAC_BITS(ADAPT_FRAC_BITS)
    ) u_rx (
        .rx_clk(clk),
        .rx_rst_n(rst_n),
        .rx_enable(rx_core_enable),
        .rx_flush(rx_flush),
        .rx_align(1'b0),
        .rx_serial_in(rx_core_serial_in),
        .rx_clear(rx_clear),
        .m_axis_tvalid(rx_axis_tvalid),
        .m_axis_tready(rx_axis_tready),
        .m_axis_tdata(rx_axis_tdata),
        .m_axis_tlast(rx_axis_tlast),
        .rx_lock(rx_lock),
        .rx_lock_stale(rx_lock_stale),
        .rx_frame_locked(rx_frame_locked),
        .rx_frame_overflow(rx_frame_overflow),
        .rx_par_valid(rx_par_valid),
        .rx_sample_strobe(rx_sample_strobe),
        .rx_serial_data(rx_serial_data),
        .rx_serial_valid(rx_serial_valid),
        .rx_freq_trim(rx_freq_trim),
        .rx_busy(rx_busy),
        .rx_clk_out(rx_clk_out)
    );

    // In word-BIST mode the RX AXIS frame is consumed by the checker instead
    // of the external m_axis port.  The checker is always ready.
    assign m_axis_tvalid  = bist_word_mode ? 1'b0 : rx_axis_tvalid;
    assign m_axis_tdata   = rx_axis_tdata;
    assign m_axis_tlast   = rx_axis_tlast;
    assign rx_axis_tready = bist_word_mode ? 1'b1 : m_axis_tready;

    prbs_word_check #(
        .WIDTH(BIST_PRBS_WIDTH),
        .DATA_WIDTH(DATA_WIDTH),
        .WORDS(WORDS),
        .SEED(BIST_SEED),
        .COMMA(BIST_COMMA)
    ) u_bist_word_check (
        .clk(clk),
        .rst_n(rst_n),
        .clear(bist_clear),
        .word_valid(rx_axis_tvalid & bist_word_mode),
        .word_last(rx_axis_tlast),
        .word_in(rx_axis_tdata),
        .error(bist_word_error),
        .error_count(bist_word_error_count),
        .frame_count(bist_word_frame_count)
    );

    serdes_bist #(
        .WIDTH(BIST_PRBS_WIDTH),
        .SEED(BIST_SEED)
    ) u_bist (
        .clk(clk),
        .rst_n(rst_n),
        .start(bist_start & bist_pad_mode),
        .clear(bist_clear),
        .serial_out(bist_serial_out),
        .serial_in(bist_check_in),
        .error(bist_error),
        .error_count(bist_error_count),
        .bit_count(bist_bit_count)
    );

    bist_snapshot #(
        .WIDTH(32)
    ) u_bist_snapshot (
        .pclk(clk),
        .presetn(rst_n),
        .req(bist_snapshot_req),
        .valid(bist_snap_valid),
        .error_count_o(bist_error_count_snap),
        .bit_count_o(bist_bit_count_snap),
        .error_o(bist_error_snap),
        .running_o(bist_running_snap),
        .clk(clk),
        .rst_n(rst_n),
        .error_count_i(bist_error_count),
        .bit_count_i(bist_bit_count),
        .error_i(bist_error),
        .running_i(bist_start & bist_pad_mode)
    );

    bist_snapshot #(
        .WIDTH(32)
    ) u_bist_word_snapshot (
        .pclk(clk),
        .presetn(rst_n),
        .req(bist_snapshot_req),
        .valid(bist_word_snap_valid),
        .error_count_o(bist_word_error_count_snap),
        .bit_count_o(bist_word_frame_count_snap),
        .error_o(bist_word_error_snap),
        .running_o(bist_word_running_snap),
        .clk(clk),
        .rst_n(rst_n),
        .error_count_i(bist_word_error_count),
        .bit_count_i(bist_word_frame_count),
        .error_i(bist_word_error),
        .running_i(bist_start & bist_word_mode)
    );

endmodule
