`timescale 1ns/1ps
`default_nettype none
// -----------------------------------------------------------------------------
// Quarter-rate 4-way Alexander BBPD vote merge.
//
// cdr_parallel_bbpd receives one 4-UI quarter-rate group per clk_q cycle and
// merges the four per-UI Alexander decisions into one summed vote:
//
//   vote_ui[i] = trans ? (edge == older_bit ? +1 : -1) : 0
//   vote       = sum_i vote_ui[i]      (-4 .. +4, signed 3 bit)
//
// Alexander pairing across the group (see sampler_bank for the phase layout,
// data taps 0.375 into each UI, edge taps 0.125 before the next boundary):
//
//   UI 0 : data = data_q[0], older = data_q[3] of the previous cycle,
//          edge = edge_q[3]  of the previous cycle (the boundary at group
//          start).  The previous cycle's pair is registered here.
//   UI i : data = data_q[i], older = data_q[i-1], edge = edge_q[i-1] (the
//          boundary that ends UI i-1 / starts UI i).
//
// Sign convention (OpenSERDES): edge sample still carries the OLD bit -> the
// clock sampled before the transition -> EARLY -> vote +1 -> the loop delays
// the sampling phase.  This is the same convention as cdr_bang_bang /
// cdr_multiphase.  Do NOT copy src/alexander_pd.v from adpll_gen: its
// early/late sign is the opposite.
//
// vote_valid asserts when at least one UI in the group had a data transition.
// A transition drought deasserts it, and the decimated loop treats the window
// as vote-less (stale/leak counters, no phase update).
// -----------------------------------------------------------------------------
module cdr_parallel_bbpd #(
    parameter int DECIM = 16           // informational: UI per loop update
) (
    input  wire               clk_q,
    input  wire               rst_n,
    input  wire               enable,
    input  wire [3:0]         data_q,    // data taps, UI order 0..3
    input  wire [3:0]         edge_q,    // edge taps, boundary order 0..3
    output reg  signed [2:0]  vote,      // summed early/late vote, -4..+4
    output reg                vote_valid // >=1 transition in this 4-UI group
);

    // Previous cycle's UI-3 pair: data (centre of UI 3) and the edge at the
    // group boundary, which is the trailing edge of the next cycle's UI 0.
    reg d3_prev_q;
    reg e3_prev_q;

    // Per-UI transition and Alexander decision.  e == older bit means the edge
    // tap sampled before the transition -> early -> +1.
    wire        trans0 = enable && (data_q[0] != d3_prev_q);
    wire        trans1 = enable && (data_q[1] != data_q[0]);
    wire        trans2 = enable && (data_q[2] != data_q[1]);
    wire        trans3 = enable && (data_q[3] != data_q[2]);

    wire signed [3:0] v0 = trans0 ? ((edge_q[3] == d3_prev_q) ? 4'sd1  : -4'sd1) : 4'sd0;
    wire signed [3:0] v1 = trans1 ? ((edge_q[0] == data_q[0]) ? 4'sd1  : -4'sd1) : 4'sd0;
    wire signed [3:0] v2 = trans2 ? ((edge_q[1] == data_q[1]) ? 4'sd1  : -4'sd1) : 4'sd0;
    wire signed [3:0] v3 = trans3 ? ((edge_q[2] == data_q[2]) ? 4'sd1  : -4'sd1) : 4'sd0;

    wire signed [4:0] v01 = v0 + v1;
    wire signed [4:0] v23 = v2 + v3;
    // Truncating signed assignment: |sum| <= 4 fits in 3 bits.
    wire signed [2:0] vote_c = v01 + v23;

    always_ff @(posedge clk_q or negedge rst_n) begin
        if (!rst_n) begin
            d3_prev_q  <= 1'b0;
            e3_prev_q  <= 1'b0;
            vote       <= 3'sd0;
            vote_valid <= 1'b0;
        end else if (enable) begin
            d3_prev_q <= data_q[3];
            e3_prev_q <= edge_q[3];
            vote       <= vote_c;
            vote_valid <= trans0 | trans1 | trans2 | trans3;
        end else begin
            vote       <= 3'sd0;
            vote_valid <= 1'b0;
        end
    end

endmodule
