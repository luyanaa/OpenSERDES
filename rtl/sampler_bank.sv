`timescale 1ns/1ps
`default_nettype none
// -----------------------------------------------------------------------------
// Quarter-rate parallel sampler bank (PMA leaf, per-PDK swappable).
//
// Eight receiver_sampler leaves (catalog entry: stdcell DFF v1, TG-latch /
// StrongARM later) capture the serial NRZ on the eight 500 MHz clock phases,
// 45 deg apart.  Within one 4-UI quarter-rate group the even phases
// (clk_ph[0,2,4,6]) sit mid-UI (the "data" taps) and the odd phases
// (clk_ph[1,3,5,7]) sit on the bit boundaries (the "edge" taps), offset by
// half a UI:
//
//   group time (ns, UI = 0.5 ns, group start = t0 = a bit boundary):
//     data_q[i]  samples data at t0 + i*0.5 + 0.1875   (0.375 into UI i)
//     edge_q[i]  samples data at t0 + i*0.5 + 0.4375   (0.875 into UI i,
//                                                       0.125 before the
//                                                       next boundary)
//
// The 0.1875 ns offset keeps every phase edge strictly inside the quarter-rate
// cycle so no sampler edge collides with the clk_q capture edge.
//
// The staggered sampler outputs are re-captured into clk_q-domain registers on
// the next posedge clk_q, so data_q/edge_q are clean quarter-rate words valid
// for one 500 MHz cycle.  This needs clk_q as an extra input beyond the spec
// sketch: the raw DFF outputs settle at different times inside the cycle and
// something has to collect them at the cycle end.
//
// Stage B: clk_ph comes from the real multiphase_clock / PI.  pi_code from
// cdr_decimated_loop either re-times clk_ph directly (walk the sampling
// phases) or selects between two adjacent DCO phases; the capture scheme
// above is unchanged as long as the phase grid keeps the same 45 deg pitch
// and stays clear of the clk_q edge.  In this v1 the TB supplies an ideal
// 8-phase clock and the phase walk is emulated by the pi_code rotator in
// cdr_parallel_top.
// -----------------------------------------------------------------------------
module sampler_bank (
    input  wire       data_in,          // serial NRZ
    input  wire [7:0] clk_ph,           // 8 phases, 45 deg apart @ 500 MHz
    input  wire       clk_q,            // quarter-rate capture clock (500 MHz)
    input  wire       rst_n,
    output reg  [3:0] data_q,           // samples on clk_ph[0,2,4,6]
    output reg  [3:0] edge_q,           // samples on clk_ph[1,3,5,7]
    output reg  [7:0] raw_q             // all 8, for the pi_code rotator in top
);

    wire [7:0] raw_smp;

    genvar g;
    generate
        for (g = 0; g < 8; g = g + 1) begin : g_smp
            receiver_sampler u_smp (
                .clk    (clk_ph[g]),
                .rst_n  (rst_n),
                .enable (1'b1),
                .d      (data_in),
                .q      (raw_smp[g])
            );
        end
    endgenerate

    // Collect the staggered samples at the end of the quarter-rate cycle.
    // Every clk_ph edge of this group happened at or before t0+1.9375 ns, so
    // raw_q is stable before the next clk_q edge (t0+2.0 ns).
    always_ff @(posedge clk_q or negedge rst_n) begin
        if (!rst_n) begin
            data_q <= 4'b0000;
            edge_q <= 4'b0000;
            raw_q  <= 8'b0000_0000;
        end else begin
            data_q <= {raw_smp[6], raw_smp[4], raw_smp[2], raw_smp[0]};
            edge_q <= {raw_smp[7], raw_smp[5], raw_smp[3], raw_smp[1]};
            raw_q  <= raw_smp;
        end
    end

endmodule
