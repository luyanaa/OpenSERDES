`timescale 1ns/1ps

// AXI4-Stream RX endpoint: serial line -> CDR/deserializer -> AXIS words.
// AXIS and rx_clk are the same clock in this wrapper; add an AXIS async FIFO
// when the SoC clock is different.
module serdes_rx_axis_digital #(
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
    parameter int ADAPT_LOOP_GAIN = 4
) (
    input  logic                              rx_clk,
    input  logic                              rx_rst_n,
    input  logic                              rx_enable,
    input  logic                              rx_flush,
    input  logic                              rx_align,
    input  logic                              rx_serial_in,
    input  logic                              rx_clear,

    output logic                              m_axis_tvalid,
    input  logic                              m_axis_tready,
    output logic [DATA_WIDTH-1:0]             m_axis_tdata,
    output logic                              m_axis_tlast,

    output logic                              rx_lock,
    output logic                              rx_lock_stale,
    output logic                              rx_frame_locked,
    output logic                              rx_frame_overflow,
    output logic                              rx_par_valid,
    output logic                              rx_sample_strobe,
    output logic                              rx_serial_data,
    output logic                              rx_serial_valid,
    output logic signed [ADAPT_FRAC_BITS:0]   rx_freq_trim,
    output logic                              rx_busy,
    output logic                              rx_clk_out
);

    logic rx_rst_n_sync;
    logic [WORDS-1:0][DATA_WIDTH-1:0] par_data;
    logic frame_valid;

    reset_sync #(.STAGES(2)) u_reset_sync (
        .clk(rx_clk),
        .rst_n_in(rx_rst_n),
        .rst_n_out(rx_rst_n_sync)
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
        .rx_par_data(par_data),
        .rx_par_valid(rx_par_valid),
        .rx_busy(rx_busy),
        .rx_lock(rx_lock),
        .rx_lock_stale(rx_lock_stale),
        .rx_sample_strobe(rx_sample_strobe),
        .rx_clk_out(rx_clk_out),
        .rx_frame_locked(rx_frame_locked),
        .rx_frame_valid(frame_valid),
        .rx_serial_data(rx_serial_data),
        .rx_serial_valid(rx_serial_valid),
        .rx_freq_trim(rx_freq_trim)
    );

    axis_rx_adapter #(
        .DATA_WIDTH(DATA_WIDTH),
        .WORDS(WORDS)
    ) u_axis (
        .clk(rx_clk),
        .rst_n(rx_rst_n_sync),
        .clear(rx_clear | rx_flush),
        .par_valid(frame_valid),
        .par_data(par_data),
        .m_axis_tvalid(m_axis_tvalid),
        .m_axis_tready(m_axis_tready),
        .m_axis_tdata(m_axis_tdata),
        .m_axis_tlast(m_axis_tlast),
        .frame_overflow(rx_frame_overflow)
    );

endmodule
