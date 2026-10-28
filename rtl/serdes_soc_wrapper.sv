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
// -----------------------------------------------------------------------------
module serdes_soc_wrapper #(
    parameter int DATA_WIDTH = 8,
    parameter int WORDS      = 4,
    parameter bit LSB_FIRST  = 1'b1,
    parameter int SYNC_LEN   = 8,
    parameter logic [SYNC_LEN-1:0] SYNC_PATTERN = 'hBC,
    parameter int ADAPT_FRAC_BITS = 12
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
    output logic [7:0][31:0]                  csr_regs
);

    localparam int TX_WORDS = WORDS + 1;
    localparam int RX_WORDS = WORDS;

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

    assign tx_enable = csr_regs[0][0];
    assign rx_enable = csr_regs[0][1];
    assign rx_flush  = csr_regs[0][2];
    assign tx_clear  = csr_regs[0][3];
    assign rx_clear  = csr_regs[0][4];

    assign status = {
        22'b0,
        rx_busy, rx_par_valid, rx_frame_overflow, rx_frame_locked,
        rx_lock_stale, rx_lock, tx_frame_error, tx_frame_done,
        tx_busy, s_axis_tready
    };

    apb_csr #(
        .NUM_REGS(8),
        .DATA_WIDTH(32),
        .ADDR_WIDTH(16)
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
        .status_i(status)
    );

    serdes_tx_axis_digital #(
        .DATA_WIDTH(DATA_WIDTH),
        .WORDS(TX_WORDS),
        .LSB_FIRST(LSB_FIRST)
    ) u_tx (
        .tx_clk(clk),
        .tx_rst_n(rst_n),
        .tx_enable(tx_enable),
        .tx_clear(tx_clear),
        .s_axis_tvalid(s_axis_tvalid),
        .s_axis_tready(s_axis_tready),
        .s_axis_tdata(s_axis_tdata),
        .s_axis_tlast(s_axis_tlast),
        .tx_serial_out(tx_serial_out),
        .tx_serial_valid(tx_serial_valid),
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
        .rx_enable(rx_enable),
        .rx_flush(rx_flush),
        .rx_align(1'b0),
        .rx_serial_in(rx_serial_in),
        .rx_clear(rx_clear),
        .m_axis_tvalid(m_axis_tvalid),
        .m_axis_tready(m_axis_tready),
        .m_axis_tdata(m_axis_tdata),
        .m_axis_tlast(m_axis_tlast),
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

endmodule
