`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// Receiver digital sampling boundary.
//
// The original receiver (Receiver.sp) ends in a custom 2 GHz NAND-based
// flip-flop.  This behavioral model keeps only the digital contract:
//
//   - q captures d on a rising clk edge while enable is high;
//   - q holds when enable is low;
//   - asynchronous active-low reset clears q.
//
// It intentionally does not model the resistive-feedback inverter, the CMOS
// inverter, the NAND latch's analog setup/hold behaviour, or the complementary
// output.  Those remain part of the receiver hard macro.
// -----------------------------------------------------------------------------
module receiver_sampler (
    input  logic clk,
    input  logic rst_n,
    input  logic enable,
    input  logic d,
    output logic q
);

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            q <= 1'b0;
        else if (enable)
            q <= d;
    end

endmodule
