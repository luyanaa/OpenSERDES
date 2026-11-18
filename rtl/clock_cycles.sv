`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// Clock cycle measurement, coherently readable from another clock domain.
//
// A free-running Gray counter in the measured domain, 2FF-synchronised into the
// reader's domain, then re-decoded to binary.  Gray coding is what makes this
// safe without a handshake: only one bit changes per measured cycle, so a torn
// read is impossible and the recovered value is at most a couple of cycles
// stale.
//
// This is the measured counterpart of the ppm term in
// verification/channel/phase_model.py: the model predicts a frequency offset,
// this measures it.  Two instances (one per direction) plus a latch give a
// matched pair, which is how a serial link's ppm is actually observed on a
// board.
//
// Reference: LiteICLink `serdes/common.py::ClockCycles` -- same construction
// (GrayCounter + MultiReg + per-bit XOR decode), which their unit tests use to
// measure linerate on hardware.
// -----------------------------------------------------------------------------
module clock_cycles #(
    parameter int WIDTH = 32,
    // Synchroniser depth in the reading domain.
    parameter int SYNC_STAGES = 2
) (
    // Measured clock domain.
    input  logic             sclk,
    input  logic             srst_n,
    // Reading clock domain.
    input  logic             dclk,
    input  logic             drst_n,
    // Binary cycle count, in the reading domain.
    output logic [WIDTH-1:0] value
);
    localparam int W = (SYNC_STAGES < 2) ? 2 : SYNC_STAGES;

    // ---- measured domain: binary count + its Gray image --------------------
    logic [WIDTH-1:0] bin_q;
    logic [WIDTH-1:0] gray_q;

    assign gray_q = bin_q ^ (bin_q >> 1);

    always_ff @(posedge sclk or negedge srst_n) begin
        if (!srst_n)
            bin_q <= '0;
        else
            bin_q <= bin_q + 1'b1;
    end

    // ---- reading domain: synchronise, then decode --------------------------
    logic [W-1:0][WIDTH-1:0] sync_q;
    logic [WIDTH-1:0]        gray_sync;

    always_ff @(posedge dclk or negedge drst_n) begin
        if (!drst_n)
            sync_q <= '0;
        else
            sync_q <= {sync_q[W-2:0], gray_q};
    end

    assign gray_sync = sync_q[W-1];

    // Binary bit n is the XOR of every Gray bit from n upwards.
    always_comb begin
        for (int b = 0; b < WIDTH; b++)
            value[b] = ^(gray_sync >> b);
    end

endmodule
