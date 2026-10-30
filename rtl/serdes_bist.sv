`timescale 1ns/1ps

// PRBS BIST wrapper: generator and checker share start/clear and a seed.
// The serial output is intended to be looped back or driven into the link;
// serial_in is the received bit.  This is a link/BIST primitive, not an APB
// CSR block yet.
module serdes_bist #(
    parameter int WIDTH = 7,
    parameter logic [WIDTH-1:0] SEED = '1
) (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        start,
    input  logic        clear,
    output logic        serial_out,
    input  logic        serial_in,
    output logic        error,
    output logic        error_pulse,
    output logic [31:0] error_count,
    output logic [31:0] bit_count
);

    prbs_gen #(.WIDTH(WIDTH), .SEED(SEED)) u_gen (
        .clk(clk),
        .rst_n(rst_n),
        .clear(clear),
        .enable(start),
        .bit_out(serial_out)
    );

    prbs_check #(.WIDTH(WIDTH), .SEED(SEED)) u_check (
        .clk(clk),
        .rst_n(rst_n),
        .clear(clear),
        .enable(start),
        .bit_in(serial_in),
        .error(error),
        .error_pulse(error_pulse),
        .error_count(error_count),
        .bit_count(bit_count)
    );

endmodule
