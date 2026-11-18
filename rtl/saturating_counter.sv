`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// Saturating, clearable event counter.
//
// Every link-health number a protocol PHY reads (decode errors, link drops,
// timeouts) has the same shape: increment on a pulse, never wrap, clear from the
// control plane.  Wrapping is worse than useless here: a link that produces
// 2^32 errors and one that produces 0 look identical again.
//
// `saturated` is asserted once the counter has reached its maximum and stays
// asserted until `clear`, so software can tell "no errors" from "we stopped
// counting".
// -----------------------------------------------------------------------------
module saturating_counter #(
    parameter int WIDTH = 32
) (
    input  logic             clk,
    input  logic             rst_n,
    input  logic             clear,
    input  logic             inc,
    output logic [WIDTH-1:0] value,
    output logic             saturated
);
    logic [WIDTH-1:0] count_q;

    assign value     = count_q;
    assign saturated = &count_q;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            count_q <= '0;
        else if (clear)
            count_q <= '0;
        else if (inc && !(&count_q))
            count_q <= count_q + 1'b1;
    end

endmodule
