`timescale 1ns/1ps
// Behavioural DLL phase mux + sampler.
//
// The model samples data_in at (tap + 0.5) * SLOT_NS after the clk edge and
// presents the result for the digital core to read on the following edge.
//
// The order of operations matters.  A nonblocking assignment with an
// intra-assignment delay (`smp <= #D data_in`) evaluates data_in at the
// current time and only delays the update; that models a fixed-edge sampler
// with a delayed output, not a phase-selected one.  Here the block first
// waits to the sampling instant and then reads data_in, so a data transition
// inside the same 10 ns window is seen by the taps whose phase is after it
// and missed by the taps before it.  tb_dll_sampler.sv checks exactly that.
//
// The delay is < SLOT_NS * (2^PH_W - 0.5), so the block always finishes
// before the next clk edge for the 8-tap/10 ns configuration.
module dll_sampler_model #(
    parameter real SLOT_NS      = 1.25,
    parameter real EDGE_SETTLE  = 0.01
) (
    input  logic       clk,
    input  logic       rst_n,
    input  logic       data_in,
    input  logic [2:0] tap,
    output logic       smp
);
    always @(posedge clk) begin
        if (rst_n) begin
            int tap_snapshot;
            #(EDGE_SETTLE);
            // Read the tap after the DUT's nonblocking update has settled.
            tap_snapshot = int'(tap);
            #((tap_snapshot + 0.5) * SLOT_NS);
            // Read the line at the selected phase, then hold until the next
            // edge.  A blocking assignment is intentional: there is no
            // second delay between the read and the core's next edge.
            smp = data_in;
        end
    end
endmodule
