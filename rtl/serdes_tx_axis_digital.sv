`timescale 1ns/1ps

// AXI4-Stream TX endpoint: AXIS words -> serializer_stream -> serial line.
// The AXIS clock and the serial bit clock are the same tx_clk in this wrapper;
// use an AXIS async FIFO in the SoC wrapper when they differ.
module serdes_tx_axis_digital #(
    parameter int DATA_WIDTH = 32,
    parameter int WORDS      = 8,
    parameter bit LSB_FIRST  = 1'b1,
    parameter bit CRC_ENABLE = 1'b0,
    parameter int CRC_WIDTH  = 16,
    parameter logic [CRC_WIDTH-1:0] CRC_POLY = 16'h1021,
    parameter logic [CRC_WIDTH-1:0] CRC_INIT = '1,
    parameter bit CRC_LSB_FIRST = 1'b0,
    parameter bit CRC_REFLECT_OUT = 1'b0,
    parameter logic [CRC_WIDTH-1:0] CRC_XOR_OUT = '0
) (
    input  logic                  tx_clk,
    input  logic                  tx_rst_n,
    input  logic                  tx_enable,
    input  logic                  tx_clear,

    input  logic                  s_axis_tvalid,
    output logic                  s_axis_tready,
    input  logic [DATA_WIDTH-1:0] s_axis_tdata,
    input  logic                  s_axis_tlast,

    output logic                  tx_serial_out,
    output logic                  tx_serial_valid,
    output logic                  tx_busy,
    output logic                  tx_frame_done,
    output logic                  tx_frame_error
);

    localparam int CRC_BYTES = (CRC_WIDTH + DATA_WIDTH - 1) / DATA_WIDTH;
    localparam int TX_WORDS = CRC_ENABLE ? (WORDS + CRC_BYTES) : WORDS;

    logic tx_rst_n_sync;
    logic in_valid;
    logic in_ready;
    logic [DATA_WIDTH-1:0] in_word;
    logic axis_tvalid, axis_tready, axis_tlast;
    logic [DATA_WIDTH-1:0] axis_tdata;

    reset_sync #(.STAGES(2)) u_reset_sync (
        .clk(tx_clk),
        .rst_n_in(tx_rst_n),
        .rst_n_out(tx_rst_n_sync)
    );

    generate
        if (CRC_ENABLE) begin : g_crc
            /* verilator lint_off PINCONNECTEMPTY */
            axis_crc_inserter #(
                .DATA_WIDTH(DATA_WIDTH), .CRC_WIDTH(CRC_WIDTH),
                .POLY(CRC_POLY), .INIT(CRC_INIT), .LSB_FIRST(CRC_LSB_FIRST),
                .REFLECT_OUT(CRC_REFLECT_OUT), .XOR_OUT(CRC_XOR_OUT)
            ) u_crc (
                .clk(tx_clk), .rst_n(tx_rst_n_sync), .enable(tx_enable),
                .s_axis_tvalid(s_axis_tvalid), .s_axis_tready(s_axis_tready),
                .s_axis_tdata(s_axis_tdata), .s_axis_tlast(s_axis_tlast),
                .m_axis_tvalid(axis_tvalid), .m_axis_tready(axis_tready),
                .m_axis_tdata(axis_tdata), .m_axis_tlast(axis_tlast),
                .m_axis_tuser(), .crc_word()
            );
            /* verilator lint_on PINCONNECTEMPTY */
        end else begin : g_no_crc
            assign axis_tvalid  = s_axis_tvalid;
            assign s_axis_tready = axis_tready;
            assign axis_tdata   = s_axis_tdata;
            assign axis_tlast   = s_axis_tlast;
        end
    endgenerate

    axis_tx_adapter #(
        .DATA_WIDTH(DATA_WIDTH),
        .WORDS(TX_WORDS)
    ) u_axis (
        .clk(tx_clk),
        .rst_n(tx_rst_n_sync),
        .clear(tx_clear),
        .s_axis_tvalid(axis_tvalid),
        .s_axis_tready(axis_tready),
        .s_axis_tdata(axis_tdata),
        .s_axis_tlast(axis_tlast),
        .in_valid(in_valid),
        .in_ready(in_ready),
        .in_word(in_word),
        .frame_error(tx_frame_error)
    );

    serializer_stream #(
        .DATA_WIDTH(DATA_WIDTH),
        .WORDS(TX_WORDS),
        .LSB_FIRST(LSB_FIRST)
    ) u_serializer (
        .clk(tx_clk),
        .rst_n(tx_rst_n_sync),
        .enable(tx_enable),
        .in_valid(in_valid),
        .in_ready(in_ready),
        .in_word(in_word),
        .serial_out(tx_serial_out),
        .serial_valid(tx_serial_valid),
        .busy(tx_busy),
        .frame_done(tx_frame_done)
    );

endmodule
