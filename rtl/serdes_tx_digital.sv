`timescale 1ns/1ps

// Digital TX endpoint: serializer + reset synchronizer.
// This wrapper can be synthesized on a different PDK from the RX endpoint.
module serdes_tx_digital #(
    parameter int DATA_WIDTH   = 32,
    parameter int WORDS        = 8,
    parameter bit LSB_FIRST    = 1'b1,
    parameter bit CAPTURE_MODE = 1'b0,
    // PINGPONG=1 selects the dual-bank serializer (item 10): ready stays high
    // on back-to-back frames instead of dropping for WORDS capture cycles, at
    // the cost of one extra WORDS x DATA_WIDTH register bank.
    parameter bit PINGPONG     = 1'b0
) (
    input  logic                              tx_clk,
    input  logic                              tx_rst_n,
    input  logic                              tx_enable,
    input  logic                              tx_load,
    input  logic [WORDS-1:0][DATA_WIDTH-1:0]  tx_par_data,
    output logic                              tx_ready,
    output logic                              tx_serial_out,
    output logic                              tx_serial_valid,
    output logic                              tx_busy,
    output logic                              tx_frame_done
);

    logic tx_rst_n_sync;

    reset_sync #(.STAGES(2)) u_reset_sync (
        .clk(tx_clk),
        .rst_n_in(tx_rst_n),
        .rst_n_out(tx_rst_n_sync)
    );

    generate
        if (PINGPONG) begin : g_pingpong
            serializer_pingpong #(
                .DATA_WIDTH(DATA_WIDTH),
                .WORDS(WORDS),
                .LSB_FIRST(LSB_FIRST)
            ) u_serializer (
                .clk(tx_clk),
                .rst_n(tx_rst_n_sync),
                .enable(tx_enable),
                .load(tx_load),
                .par_data(tx_par_data),
                .ready(tx_ready),
                .serial_out(tx_serial_out),
                .serial_valid(tx_serial_valid),
                .busy(tx_busy),
                .frame_done(tx_frame_done)
            );
        end else begin : g_single
            serializer #(
                .DATA_WIDTH(DATA_WIDTH),
                .WORDS(WORDS),
                .LSB_FIRST(LSB_FIRST),
                .CAPTURE_MODE(CAPTURE_MODE)
            ) u_serializer (
                .clk(tx_clk),
                .rst_n(tx_rst_n_sync),
                .enable(tx_enable),
                .load(tx_load),
                .par_data(tx_par_data),
                .ready(tx_ready),
                .serial_out(tx_serial_out),
                .serial_valid(tx_serial_valid),
                .busy(tx_busy),
                .frame_done(tx_frame_done)
            );
        end
    endgenerate

endmodule
