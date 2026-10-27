`timescale 1ns/1ps

// Digital TX endpoint using the word-streaming serializer architecture.
// The external interface is a per-word valid/ready handshake instead of a
// WORDS x DATA_WIDTH parallel frame.
module serdes_tx_stream_digital #(
    parameter int DATA_WIDTH = 32,
    parameter int WORDS      = 8,
    parameter bit LSB_FIRST  = 1'b1
) (
    input  logic                  tx_clk,
    input  logic                  tx_rst_n,
    input  logic                  tx_enable,
    input  logic                  tx_in_valid,
    output logic                  tx_in_ready,
    input  logic [DATA_WIDTH-1:0] tx_in_word,
    output logic                  tx_serial_out,
    output logic                  tx_serial_valid,
    output logic                  tx_busy,
    output logic                  tx_frame_done
);

    logic tx_rst_n_sync;

    reset_sync #(.STAGES(2)) u_reset_sync (
        .clk(tx_clk),
        .rst_n_in(tx_rst_n),
        .rst_n_out(tx_rst_n_sync)
    );

    serializer_stream #(
        .DATA_WIDTH(DATA_WIDTH),
        .WORDS(WORDS),
        .LSB_FIRST(LSB_FIRST)
    ) u_serializer (
        .clk(tx_clk),
        .rst_n(tx_rst_n_sync),
        .enable(tx_enable),
        .in_valid(tx_in_valid),
        .in_ready(tx_in_ready),
        .in_word(tx_in_word),
        .serial_out(tx_serial_out),
        .serial_valid(tx_serial_valid),
        .busy(tx_busy),
        .frame_done(tx_frame_done)
    );

endmodule
