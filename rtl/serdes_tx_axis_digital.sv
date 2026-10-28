`timescale 1ns/1ps

// AXI4-Stream TX endpoint: AXIS words -> serializer_stream -> serial line.
// The AXIS clock and the serial bit clock are the same tx_clk in this wrapper;
// use an AXIS async FIFO in the SoC wrapper when they differ.
module serdes_tx_axis_digital #(
    parameter int DATA_WIDTH = 32,
    parameter int WORDS      = 8,
    parameter bit LSB_FIRST  = 1'b1
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

    logic tx_rst_n_sync;
    logic in_valid;
    logic in_ready;
    logic [DATA_WIDTH-1:0] in_word;

    reset_sync #(.STAGES(2)) u_reset_sync (
        .clk(tx_clk),
        .rst_n_in(tx_rst_n),
        .rst_n_out(tx_rst_n_sync)
    );

    axis_tx_adapter #(
        .DATA_WIDTH(DATA_WIDTH),
        .WORDS(WORDS)
    ) u_axis (
        .clk(tx_clk),
        .rst_n(tx_rst_n_sync),
        .clear(tx_clear),
        .s_axis_tvalid(s_axis_tvalid),
        .s_axis_tready(s_axis_tready),
        .s_axis_tdata(s_axis_tdata),
        .s_axis_tlast(s_axis_tlast),
        .in_valid(in_valid),
        .in_ready(in_ready),
        .in_word(in_word),
        .frame_error(tx_frame_error)
    );

    serializer_stream #(
        .DATA_WIDTH(DATA_WIDTH),
        .WORDS(WORDS),
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
