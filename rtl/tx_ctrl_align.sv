`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// Align a direct control input with the data already in the transmit pipeline.
//
// A control input that reaches the transmitter immediately (a serializer enable,
// an electrical-idle request, a driver inhibit) overtakes the symbols still in
// flight -- the lane datapath, the 8b/10b stage, the gearbox, the serializer
// shift register.  Whatever was in the pipeline is then cut or truncated.
//
// Reference: LitePCIe PR #250 hits exactly this on the ECP5 DCU, where
// `FFC_EI_EN` is a direct input while the EIOS is still in the TX pipeline, and
// fixes it by delaying the request; the Xilinx transceivers ship
// `TX_EIDLE_ASSERT_DELAY` for the same reason.  Their finding is that a *fixed*
// delay is not good enough either: the right value depends on the pipeline
// depth, so it must be a tunable.
//
// This module is the fabric equivalent.  `DELAY_CYCLES` shifts the control word
// through a register pipeline, so the request lands exactly `DELAY_CYCLES`
// later -- nominally the pipeline depth of the transmitter).
//
// `bypass` (0 delay) is the "preempt is what I want" case, e.g. `clear`:
// discarding in-flight data is the point.  Making both available means the
// choice is explicit rather than accidental.
// -----------------------------------------------------------------------------
/* verilator lint_off UNUSEDSIGNAL */
module tx_ctrl_align #(
    parameter int DELAY_CYCLES = 0
) (
    input  logic clk,
    input  logic rst_n,
    input  logic [0:0] ctrl_in,
    output logic [0:0] ctrl_out
);
    generate
        if (DELAY_CYCLES == 0) begin : g_bypass
            // No storage: clk/rst_n are intentionally unused in this branch.
            assign ctrl_out = ctrl_in;
        end else if (DELAY_CYCLES == 1) begin : g_delay1
            logic sr_q;

            always_ff @(posedge clk or negedge rst_n) begin
                if (!rst_n)
                    sr_q <= 1'b0;
                else
                    sr_q <= ctrl_in;
            end

            assign ctrl_out = sr_q;
        end else begin : g_delay
            logic [DELAY_CYCLES-1:0] sr_q;

            always_ff @(posedge clk or negedge rst_n) begin
                if (!rst_n)
                    sr_q <= '0;
                else
                    sr_q <= {sr_q[DELAY_CYCLES-2:0], ctrl_in};
            end

            assign ctrl_out = sr_q[DELAY_CYCLES-1];
        end
    endgenerate

endmodule

/* verilator lint_on UNUSEDSIGNAL */
