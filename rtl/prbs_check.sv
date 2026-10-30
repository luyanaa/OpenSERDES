`timescale 1ns/1ps

// Serial PRBS checker (PRBS7/15/31), with sticky error and counters.
module prbs_check #(
    parameter int WIDTH = 7,
    parameter logic [WIDTH-1:0] SEED = '1
) (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        clear,
    input  logic        enable,
    input  logic        bit_in,
    output logic        error,
    output logic        error_pulse,
    output logic [31:0] error_count,
    output logic [31:0] bit_count
);

    logic [WIDTH-1:0] state_q;
    logic             feedback;
    logic             expected;
    logic             error_q;
    logic [31:0]      error_count_q;
    logic [31:0]      bit_count_q;

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

    assign expected = state_q[WIDTH-1];

    assign error       = error_q;
    assign error_pulse = enable && (bit_in !== expected);
    assign error_count = error_count_q;
    assign bit_count   = bit_count_q;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state_q       <= SEED;
            error_q       <= 1'b0;
            error_count_q <= '0;
            bit_count_q   <= '0;
        end else if (clear) begin
            state_q       <= SEED;
            error_q       <= 1'b0;
            error_count_q <= '0;
            bit_count_q   <= '0;
        end else if (enable) begin
            if (bit_in !== expected) begin
                error_q       <= 1'b1;
                error_count_q <= error_count_q + 1'b1;
            end
            state_q     <= {state_q[WIDTH-2:0], feedback};
            bit_count_q <= bit_count_q + 1'b1;
        end
    end

endmodule
