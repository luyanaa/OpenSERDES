`timescale 1ns/1ps

// Two-bit pair sampler for the half-rate RX prototype.
//
// Samples serial_in on every clk_bit edge into a 2-bit history, then presents
// the previous two bits (bit0 older, bit1 newer) on clk_word.  This models the
// small full-rate sampler/front-end; the parallel deserializer bank runs on
// clk_word.  It is not a CDR or a DDR output sampler.
module receiver_pair_sampler (
    input  logic clk_bit,
    input  logic clk_word,
    input  logic rst_n,
    input  logic enable,
    input  logic serial_in,
    output logic pair_valid,
    output logic bit0,
    output logic bit1
);

    logic sr0_q;
    logic sr1_q;

    always_ff @(posedge clk_bit or negedge rst_n) begin
        if (!rst_n) begin
            sr0_q <= 1'b0;
            sr1_q <= 1'b0;
        end else if (enable) begin
            sr0_q <= serial_in;
            sr1_q <= sr0_q;
        end
    end

    always_ff @(posedge clk_word or negedge rst_n) begin
        if (!rst_n) begin
            bit0       <= 1'b0;
            bit1       <= 1'b0;
            pair_valid <= 1'b0;
        end else begin
            bit0       <= sr1_q;
            bit1       <= sr0_q;
            pair_valid <= enable;
        end
    end

endmodule
