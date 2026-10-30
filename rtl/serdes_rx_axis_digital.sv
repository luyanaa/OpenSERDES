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
    parameter int ADAPT_LOOP_GAIN = 4,
    parameter int PHASE_CTRL_W = (OVERSAMPLE <= 1) ? 1 : $clog2(OVERSAMPLE),
    parameter bit CRC_ENABLE = 1'b0,
    parameter int CRC_WIDTH  = 16,
    parameter logic [CRC_WIDTH-1:0] CRC_POLY = 16'h1021,
    parameter logic [CRC_WIDTH-1:0] CRC_INIT = '1,
    parameter bit CRC_LSB_FIRST = 1'b0,
    parameter bit CRC_REFLECT_OUT = 1'b0,
    parameter logic [CRC_WIDTH-1:0] CRC_XOR_OUT = '0
) (
    input  logic                              rx_clk,
    input  logic                              rx_rst_n,
    input  logic                              rx_enable,
    input  logic                              rx_flush,
    input  logic                              rx_align,
    input  logic                              rx_serial_in,
    input  logic [PHASE_CTRL_W-1:0]           phase_offset,
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
    output logic                              rx_clk_out,
    output logic                              crc_error
);

    localparam int CRC_BYTES = (CRC_WIDTH + DATA_WIDTH - 1) / DATA_WIDTH;
    localparam int RX_WORDS = CRC_ENABLE ? (WORDS + CRC_BYTES) : WORDS;

    logic rx_rst_n_sync;
    logic [RX_WORDS-1:0][DATA_WIDTH-1:0] par_data;
    logic frame_valid;
    /* verilator lint_off UNUSEDSIGNAL */
    logic adapter_tvalid, adapter_tready, adapter_tlast, adapter_tuser;
    /* verilator lint_on UNUSEDSIGNAL */
    logic [DATA_WIDTH-1:0] adapter_tdata;

    reset_sync #(.STAGES(2)) u_reset_sync (
        .clk(rx_clk),
        .rst_n_in(rx_rst_n),
        .rst_n_out(rx_rst_n_sync)
    );

    serdes_rx_digital #(
        .DATA_WIDTH(DATA_WIDTH),
        .WORDS(RX_WORDS),
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
        .WORDS(RX_WORDS),
        .PAYLOAD_WORDS(WORDS)
    ) u_axis (
        .clk(rx_clk),
        .rst_n(rx_rst_n_sync),
        .clear(rx_clear | rx_flush),
        .par_valid(frame_valid),
        .par_data(par_data),
        .m_axis_tvalid(adapter_tvalid),
        .m_axis_tready(adapter_tready),
        .m_axis_tdata(adapter_tdata),
        .m_axis_tlast(adapter_tlast),
        .m_axis_tuser(adapter_tuser),
        .frame_overflow(rx_frame_overflow)
    );

    generate
        if (CRC_ENABLE) begin : g_crc
            /* verilator lint_off PINCONNECTEMPTY */
            axis_crc_checker #(
                .DATA_WIDTH(DATA_WIDTH), .CRC_WIDTH(CRC_WIDTH),
                .POLY(CRC_POLY), .INIT(CRC_INIT), .LSB_FIRST(CRC_LSB_FIRST),
                .REFLECT_OUT(CRC_REFLECT_OUT), .XOR_OUT(CRC_XOR_OUT)
            ) u_crc (
                .clk(rx_clk), .rst_n(rx_rst_n_sync), .enable(rx_enable),
                .s_axis_tvalid(adapter_tvalid), .s_axis_tready(adapter_tready),
                .s_axis_tdata(adapter_tdata), .s_axis_tlast(adapter_tlast),
                .s_axis_tuser(adapter_tuser),
                .m_axis_tvalid(m_axis_tvalid), .m_axis_tready(m_axis_tready),
                .m_axis_tdata(m_axis_tdata), .m_axis_tlast(m_axis_tlast),
                .crc_error(crc_error), .crc_ok()
            );
            /* verilator lint_on PINCONNECTEMPTY */
        end else begin : g_no_crc
            assign m_axis_tvalid = adapter_tvalid;
            assign adapter_tready = m_axis_tready;
            assign m_axis_tdata  = adapter_tdata;
            assign m_axis_tlast  = adapter_tlast;
            assign crc_error     = 1'b0;
        end
    endgenerate

endmodule
