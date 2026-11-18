`timescale 1ns/1ps

/* verilator lint_off DECLFILENAME */

// -----------------------------------------------------------------------------
// AXI4-Stream width bonding layer for serdes_multilane_top.
//
// The multi-lane core works on one DATA_WIDTH word per lane per beat: that is
// the lockstep stripe the deskew produces and the only shape in which the lanes
// stay word-aligned.  A SoC port is normally one wide AXI4-Stream, so the
// bonding layer is the width adapter between the two:
//
//   axis_lane_bonder      : one LANES*DATA_WIDTH beat  -> LANES parallel words
//   axis_lane_debonder    : LANES parallel words       -> one wide beat
//
// Both are pure handshake converters: no storage, no reordering.  The deskew
// already guarantees that lane word n is the same payload word on every lane,
// so a wide beat is just the concatenation and the lane order is fixed by the
// channel order (lane 0 in the low bits).
//
// tlast is NOT carried here.  Frame boundaries on this link are an in-band
// ordered set (the coding layer's /T/, an IEEE all-control block, or the
// packetizer's fragment header), because a sideband cannot survive the
// per-lane serial link and the deskew only forwards words.  Use
// axis_frame_packetizer / axis_reliable_link either side of the bonder when a
// framed stream is needed.
//
// The debonder gates tvalid with `link_ok`: while the deskew is hunting, a
// lane is missing, or a lane fault is sticky, no partial stripe is handed to
// the SoC.  `underflow` counts beats dropped for that reason so a stuck link
// is visible instead of silently short.
//
// `lane_mask`/`lane_width` follow the negotiated width: a masked-out lane holds
// zeros in the wide beat and `m_axis_width` says how many lanes are valid, so
// the SoC port keeps one fixed shape and the protocol layer decides what a
// narrower link means (it is *not* silently repacked).
// -----------------------------------------------------------------------------
module axis_lane_bonder #(
    parameter int LANES       = 4,
    parameter int DATA_WIDTH  = 8,
    parameter bit ENABLE_GATE = 1'b1
) (
    input  logic                              clk,
    input  logic                              rst_n,
    input  logic                              enable,
    // Wide AXIS slave (SoC side).
    input  logic [LANES*DATA_WIDTH-1:0]       s_axis_tdata,
    input  logic                              s_axis_tvalid,
    output logic                              s_axis_tready,
    // Per-lane word stream (multilane core side).
    output logic [LANES-1:0][DATA_WIDTH-1:0]  m_lane_data,
    output logic                              m_lane_valid,
    input  logic                              m_lane_ready
);
    generate
        for (genvar l = 0; l < LANES; l++) begin : g_lane
            assign m_lane_data[l] = s_axis_tdata[l*DATA_WIDTH +: DATA_WIDTH];
        end
    endgenerate

    assign s_axis_tready = ENABLE_GATE ? (enable & m_lane_ready) : m_lane_ready;
    assign m_lane_valid  = ENABLE_GATE ? (enable & s_axis_tvalid) : s_axis_tvalid;
endmodule


module axis_lane_debonder #(
    parameter int LANES       = 4,
    parameter int DATA_WIDTH  = 8,
    parameter bit ENABLE_GATE = 1'b1
) (
    input  logic                              clk,
    input  logic                              rst_n,
    input  logic                              enable,
    // Per-lane word stream (multilane core side).
    input  logic [LANES-1:0][DATA_WIDTH-1:0]  s_lane_data,
    input  logic                              s_lane_valid,
    output logic                              s_lane_ready,
    // Link state from serdes_multilane_top.
    input  logic                              link_ok,
    // Negotiated width: masked-out lanes hold zeros, `m_axis_width` counts.
    input  logic [LANES-1:0]                  lane_mask,
    output logic [31:0]                       m_axis_width,
    // Wide AXIS master (SoC side).
    output logic [LANES*DATA_WIDTH-1:0]       m_axis_tdata,
    output logic                              m_axis_tvalid,
    input  logic                              m_axis_tready,
    // Beats suppressed because the link was not OK (saturating counter).
    output logic [15:0]                       underflow
);
    generate
        for (genvar l = 0; l < LANES; l++) begin : g_lane
            // A lane outside the negotiated width is driven to zero so the
            // wide beat keeps its shape (the protocol reads m_axis_width).
            assign m_axis_tdata[l*DATA_WIDTH +: DATA_WIDTH] =
                lane_mask[l] ? s_lane_data[l] : '0;
        end
    endgenerate

    int width_count;
    always_comb begin
        width_count = 0;
        for (int l = 0; l < LANES; l++)
            width_count = width_count + (lane_mask[l] ? 1 : 0);
    end
    assign m_axis_width = 32'(width_count);

    wire gate = ENABLE_GATE ? (enable & link_ok) : enable;

    // While the link is usable this is a plain handshake; while it is not, the
    // lane stream is consumed and dropped so the core keeps draining instead
    // of stalling behind a dead SoC port.
    assign m_axis_tvalid = gate & s_lane_valid;
    assign s_lane_ready  = gate ? m_axis_tready : 1'b1;

    // Dropped beats are counted: a deskew re-lock would otherwise look like a
    // short frame at the SoC with nothing flagging it.
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            underflow <= '0;
        else if (enable & s_lane_valid & ~gate)
            underflow <= underflow + 1'b1;
    end
endmodule

/* verilator lint_on DECLFILENAME */
