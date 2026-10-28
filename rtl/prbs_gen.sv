`timescale 1ns/1ps

// Serial PRBS generator (PRBS7/15/31).
// One bit per enabled clock.  The first bit is the MSB of SEED.
module prbs_gen #(
    parameter int WIDTH = 7,
    parameter logic [WIDTH-1:0] SEED = '1
) (
    input  logic clk,
    input  logic rst_n,
    input  logic enable,
    output logic bit_out
);

    logic [WIDTH-1:0] state_q;
    logic             feedback;

    generate
        if (WIDTH == 7) begin : g_prbs7
            assign feedback = state_q[6] ^ state_q[5];
        end else if (WIDTH == 15) begin : g_prbs15
            assign feedback = state_q[14] ^ state_q[13];
        end else if (WIDTH == 31) begin : g_prbs31
            assign feedback = state_q[30] ^ state_q[27];
        end else begin : g_prbs_other
            assign feedback = state_q[WIDTH-1] ^ state_q[WIDTH-2];
        end
    endgenerate

    // Combinational output: the checker sees the same state MSB at the same
    // edge, so generator/checker remain aligned without an extra delay flop.
    assign bit_out = state_q[WIDTH-1];

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            state_q <= SEED;
        else if (enable)
            state_q <= {state_q[WIDTH-2:0], feedback};
    end

endmodule
