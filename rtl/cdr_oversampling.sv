`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// Abstract digital oversampling CDR.
//
// This is a technology-independent replacement for the original gate-level
// CLK_RECOVERY block.  It is not a cycle-accurate clone of that netlist.
//
// Contract:
//   - clk is the oversampling clock, OVERSAMPLE times the data rate.
//   - data_in is sampled on every enabled clk.
//   - During acquisition (lock=0), a detected data edge loads the phase counter
//     with EDGE_PHASE so that the eye-centre sample lands near the middle of
//     the bit.  After lock, the phase counter free-runs at OVERSAMPLE cycles
//     per bit.  This is a frequency-locked oversampling receiver; it does not
//     implement a phase-tracking loop for plesiochronous links.
//   - data_out is sampled at the eye centre.
//   - sample_strobe pulses for one clk per bit when data_out is valid.
//   - clk_out is a divided monitor clock at the data rate.  Use sample_strobe,
//     not clk_out, to capture data_out.
//   - lock asserts after LOCK_THRESH detected edges and releases at
//     LOCK_RELEASE when no edge is seen for a bit period (hysteresis).
//
// OVERSAMPLE must be even and >= 4.  SYNC_STAGES >= 2.
// -----------------------------------------------------------------------------
module cdr_oversampling #(
    parameter int OVERSAMPLE  = 4,
    parameter int SYNC_STAGES = 2,
    parameter int LOCK_THRESH  = 8,
    parameter int LOCK_RELEASE = 4,
    parameter int LOCK_DECAY   = 1
) (
    input  logic clk,
    input  logic rst_n,
    input  logic enable,
    input  logic data_in,
    output logic data_out,
    output logic sample_strobe,
    output logic clk_out,
    output logic lock
);

    localparam int PHASE_W = (OVERSAMPLE <= 1) ? 1 : $clog2(OVERSAMPLE);
    localparam int HALF    = OVERSAMPLE / 2;
    localparam int SYNC_W  = (SYNC_STAGES < 2) ? 2 : SYNC_STAGES;
    localparam int LOCK_W  = (LOCK_THRESH <= 1) ? 1 : $clog2(LOCK_THRESH+1);

    // Edge detection occurs SYNC_W cycles after the real transition.  Load
    // the phase counter one cycle before the eye-centre phase so that the
    // first post-acquisition sample is as close to the centre as possible.
    localparam int EDGE_PHASE   = (SYNC_W < OVERSAMPLE) ? (SYNC_W - 1) : 0;
    localparam int SAMPLE_PHASE = HALF;

    localparam logic [PHASE_W-1:0] LAST_PHASE      = PHASE_W'(OVERSAMPLE-1);
    localparam logic [PHASE_W-1:0] HALF_LAST       = PHASE_W'(HALF-1);
    localparam logic [PHASE_W-1:0] SAMPLE_PHASE_C  = PHASE_W'(SAMPLE_PHASE);
    localparam logic [PHASE_W-1:0] EDGE_PHASE_C    = PHASE_W'(EDGE_PHASE);
    localparam logic [LOCK_W-1:0]  LOCK_MAX        = LOCK_W'(LOCK_THRESH);
    localparam logic [LOCK_W-1:0]  LOCK_REL        = LOCK_W'(LOCK_RELEASE);
    localparam logic [LOCK_W-1:0]  LOCK_DEC        = LOCK_W'(LOCK_DECAY);

    logic [SYNC_W-1:0]  sync_q;
    logic [PHASE_W-1:0] phase_q;
    logic [LOCK_W-1:0]  lock_cnt_q;
    logic               data_out_q;
    logic               sample_strobe_q;
    logic               clk_out_q;
    logic               lock_q;
    logic               data_edge;
    logic               edge_seen_q;
    logic               sample_now;
    logic               sample_d_q;

    assign data_edge = sync_q[SYNC_W-1] ^ sync_q[SYNC_W-2];

    // One sample per bit: the phase counter visits SAMPLE_PHASE once per wrap.
    assign sample_now = (phase_q == SAMPLE_PHASE_C);

    assign data_out      = data_out_q;
    assign sample_strobe = sample_strobe_q;
    assign clk_out       = clk_out_q;
    assign lock          = lock_q;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            sync_q          <= '0;
            phase_q         <= '0;
            lock_cnt_q      <= '0;
            data_out_q      <= 1'b0;
            sample_strobe_q <= 1'b0;
            clk_out_q       <= 1'b0;
            lock_q          <= 1'b0;
            edge_seen_q     <= 1'b0;
            sample_d_q      <= 1'b0;
        end else begin
            sync_q <= {sync_q[SYNC_W-2:0], data_in};

            if (enable) begin
                // Acquisition: align the free-running phase to the edge.
                // Tracking: keep free-running once locked.
                if (data_edge && !lock_q) begin
                    phase_q <= EDGE_PHASE_C;
                end else if (phase_q == LAST_PHASE) begin
                    phase_q <= '0;
                end else begin
                    phase_q <= phase_q + 1'b1;
                end

                // One edge per bit is enough to maintain lock.
                if (data_edge)
                    edge_seen_q <= 1'b1;
                else if (phase_q == LAST_PHASE)
                    edge_seen_q <= 1'b0;

                if (data_edge) begin
                    if (lock_cnt_q != LOCK_MAX)
                        lock_cnt_q <= lock_cnt_q + 1'b1;
                end else if (phase_q == LAST_PHASE && !edge_seen_q) begin
                    if (lock_cnt_q > LOCK_DEC)
                        lock_cnt_q <= lock_cnt_q - LOCK_DEC;
                    else
                        lock_cnt_q <= '0;
                end

                // One-cycle pipeline: capture the settled synchronizer
                // output, then present data_out and sample_strobe together.
                sample_d_q <= sample_now;
                if (sample_d_q)
                    data_out_q <= sync_q[0];
                sample_strobe_q <= sample_d_q;

                // Divided monitor clock, 50% duty for an even OVERSAMPLE.
                if (phase_q == HALF_LAST)
                    clk_out_q <= 1'b1;
                else if (phase_q == LAST_PHASE)
                    clk_out_q <= 1'b0;
            end else begin
                sample_strobe_q <= 1'b0;
            end

            // Hysteresis: assert at LOCK_THRESH, release at LOCK_RELEASE.
            if (lock_q)
                lock_q <= (lock_cnt_q >= LOCK_REL);
            else
                lock_q <= (lock_cnt_q >= LOCK_MAX);
        end
    end

endmodule
