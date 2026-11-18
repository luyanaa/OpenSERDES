`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// Lane width negotiation.
//
// A bonded link is designed for N lanes.  A real link does not always get all of
// them: a connector, a cable, a lane's CDR, or one failed channel.  Two possible
// behaviours:
//
//   WIDTH_MODE=0 (default, fixed width)  -- what this project did until now:
//       every lane must frame, otherwise the link never comes up.  Correct for a
//       chiplet link whose lanes are on the same die/package: a lane that is not
//       there is a wiring bug, not a condition to adapt to.
//
//   WIDTH_MODE=1 (negotiated)            -- what a protocol PHY wants (PCIe
//       negotiates x1/x2/x4/x8 and idles the lanes above the width): start from
//       all lanes, drop a lane that never frames, publish the effective width,
//       and keep running narrower instead of failing.
//
// This module is the second one.  It is *not* enabled by default and it makes no
// protocol decision on its own: it publishes `lane_mask` / `width` / `degraded`
// and lets the layer above decide what a narrower link means (retrain, report a
// lower capability, or fall back).  Whether a lost lane may come back is also
// the caller's choice (`AUTO_RESTORE`), because re-adding a lane mid-protocol
// is a protocol question, not a PHY question.
//
// Policy
// ------
//   - a lane in the mask that is not `lane_ok` for LANE_UP_WINDOW consecutive
//     cycles is dropped (once per `clear`/`rescan`; no flapping);
//   - a lane out of the mask that is `lane_ok` for LANE_RESTORE_WINDOW
//     consecutive cycles is re-added only if AUTO_RESTORE, else only on
//     `rescan`;
//   - the mask never goes below MIN_LANES.  `width_error` latches as soon as
//     the width *reaches* MIN_LANES: that is the floor, so the link can no
//     longer lose a lane and the protocol should report a lower capability.
//
// `lane_ok` is **activity**, not a sticky status: it must be high while the lane
// is actually delivering words.  A word-framing lock is not usable here --
// `rx_frame_locked` is sticky in this design (it never clears just because the
// line went quiet), so a lane that is physically gone would look healthy
// forever.  The top therefore drives this from the per-lane word stream.
// -----------------------------------------------------------------------------
module lane_width_negotiator #(
    parameter int LANES         = 4,
    parameter int MIN_LANES     = 1,
    // Cycles a lane may be silent before it is dropped from the link.
    parameter int LANE_UP_WINDOW = 1024,
    // Cycles a dropped lane must be good before it may come back (only used
    // when AUTO_RESTORE=1).
    parameter int LANE_RESTORE_WINDOW = 65536,
    // 0: a dropped lane comes back only on `rescan` (protocol decides).
    parameter bit AUTO_RESTORE = 1'b0,
    // 0: fixed width, mask is always all-ones (the historical behaviour).
    parameter bit WIDTH_MODE   = 1'b0
) (
    input  logic              clk,
    input  logic              rst_n,
    input  logic              enable,
    // Drops every lane's history: all lanes are eligible again.
    input  logic              clear,
    // Re-evaluate now (a protocol "rescan"), also re-arms dropped lanes.
    input  logic              rescan,
    // Per-lane liveness: high while that lane delivers words.  Must not be a
    // sticky lock (see the note above).
    input  logic [LANES-1:0]  lane_ok,
    output logic [LANES-1:0]  lane_mask,
    output logic [LANES-1:0]  lane_dropped,
    output logic [LANES-1:0]  lane_restored,
    // Number of lanes in the link, and whether it is below LANES.
    output logic [31:0]       width,
    output logic              degraded,
    // Pulses when the mask changed (the deskew must re-hunt).
    output logic              width_change,
    // The mask hit MIN_LANES and lanes had to be dropped: the link is unusable
    // at the designed width, a protocol should report a lower capability.
    output logic              width_error,
    // Saturating count of width changes, for telemetry.
    output logic [15:0]       width_change_count
);
    localparam int LW = (LANES <= 1) ? 1 : $clog2(LANES + 1);
    localparam int UW = (LANE_UP_WINDOW <= 1) ? 1 : $clog2(LANE_UP_WINDOW + 1);
    localparam int RW = (LANE_RESTORE_WINDOW <= 1) ? 1
                                                   : $clog2(LANE_RESTORE_WINDOW + 1);

    generate
        if (MIN_LANES < 1 || MIN_LANES > LANES) begin : g_bad_min_lanes
            lane_width_negotiator_needs_one_le_min_lanes_le_lanes u_reject();
        end
        if (LANE_UP_WINDOW < 2) begin : g_bad_up_window
            lane_width_negotiator_needs_up_window_ge_2 u_reject();
        end
    endgenerate

    // One-hot lane selectors; sized explicitly so every tool agrees on width.
    function automatic logic [LANES-1:0] lane_bit(input int idx);
        logic [LANES-1:0] one;
        one = '0;
        if (idx >= 0 && idx < LANES)
            one[idx] = 1'b1;
        lane_bit = one;
    endfunction

    logic [LANES-1:0]  mask_q;
    logic [LANES-1:0]  dropped_q;
    logic [LANES-1:0]  restore_ok;
    logic              changed_q;   // pulse: the mask changed
    logic [15:0]       change_cnt_q;
    logic              width_error_q;

    logic [UW-1:0]     up_cnt_q [LANES];
    logic [RW-1:0]     rs_cnt_q [LANES];

    // How many lanes are in the link.
    logic [LW-1:0] width_q;
    int            ones;

    assign lane_mask      = WIDTH_MODE ? mask_q : {LANES{1'b1}};
    assign lane_dropped   = WIDTH_MODE ? dropped_q : '0;
    assign lane_restored  = WIDTH_MODE ? restore_ok & mask_q : '0;
    assign width          = WIDTH_MODE ? 32'(width_q) : 32'(LANES);
    assign degraded       = WIDTH_MODE ? (width_q != LW'(LANES)) : 1'b0;
    assign width_change   = WIDTH_MODE ? changed_q : 1'b0;
    assign width_error    = WIDTH_MODE ? width_error_q : 1'b0;
    assign width_change_count = WIDTH_MODE ? change_cnt_q : 16'd0;

    always_comb begin
        ones = 0;
        for (int l = 0; l < LANES; l++)
            ones = ones + (mask_q[l] ? 1 : 0);
        width_q = LW'(ones);
    end

    genvar gl;
    generate
        for (gl = 0; gl < LANES; gl++) begin : g_lane
            always_ff @(posedge clk or negedge rst_n) begin
                if (!rst_n) begin
                    up_cnt_q[gl] <= '0;
                    rs_cnt_q[gl] <= '0;
                end else if (!enable || clear || rescan) begin
                    up_cnt_q[gl] <= '0;
                    rs_cnt_q[gl] <= '0;
                end else begin
                    // Silence budget for a lane that is in the link.
                    if (!lane_ok[gl]) begin
                        if (up_cnt_q[gl] != UW'(LANE_UP_WINDOW))
                            up_cnt_q[gl] <= up_cnt_q[gl] + 1'b1;
                    end else begin
                        up_cnt_q[gl] <= '0;
                    end
                    // Qualification budget for a lane that is out of the link.
                    if (lane_ok[gl]) begin
                        if (rs_cnt_q[gl] != RW'(LANE_RESTORE_WINDOW))
                            rs_cnt_q[gl] <= rs_cnt_q[gl] + 1'b1;
                    end else begin
                        rs_cnt_q[gl] <= '0;
                    end
                end
            end
        end
    endgenerate

    // A lane expires when its silence budget is spent; a dropped lane
    // qualifies to come back when its restore window is spent.  Driven from the
    // generated lanes (unpacked counters, so iverilog cannot infer a
    // sensitivity list for an always_comb over them).
    logic [LANES-1:0] expire;
    logic [LANES-1:0] qualify;

    generate
        for (gl = 0; gl < LANES; gl++) begin : g_flag
            assign expire[gl]  = WIDTH_MODE &&
                                 (up_cnt_q[gl] == UW'(LANE_UP_WINDOW));
            assign qualify[gl] = (rs_cnt_q[gl] == RW'(LANE_RESTORE_WINDOW));
        end
    endgenerate

    // Mask update: one lane at a time, lowest index first, so MIN_LANES is a
    // floor rather than a race between lanes.
    int drop_idx, add_idx;
    always_comb begin
        drop_idx = -1;
        add_idx  = -1;
        for (int l = LANES - 1; l >= 0; l--)
            if (expire[l] && mask_q[l])
                drop_idx = l;
        for (int l = LANES - 1; l >= 0; l--)
            if (!mask_q[l] && qualify[l] &&
                (AUTO_RESTORE || rescan))
                add_idx = l;
        // Only drop while above the floor.
        if (width_q <= LW'(MIN_LANES))
            drop_idx = -1;
    end

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            mask_q        <= {LANES{1'b1}};
            dropped_q     <= '0;
            restore_ok    <= '0;
            changed_q     <= '0;
            change_cnt_q  <= '0;
            width_error_q <= 1'b0;
        end else if (!enable) begin
            // A disabled link is not a degraded link: forget everything.
            mask_q        <= {LANES{1'b1}};
            dropped_q     <= '0;
            restore_ok    <= '0;
            changed_q     <= '0;
            width_error_q <= 1'b0;
        end else if (clear || rescan) begin
            mask_q        <= {LANES{1'b1}};
            dropped_q     <= '0;
            restore_ok    <= '0;
            changed_q     <= 1'b0;
            width_error_q <= 1'b0;
        end else if (!WIDTH_MODE) begin
            mask_q     <= {LANES{1'b1}};
            dropped_q  <= '0;
            restore_ok <= '0;
            changed_q  <= 1'b0;
        end else begin
            changed_q  <= '0;
            restore_ok <= '0;
            if (add_idx >= 0) begin
                mask_q     <= mask_q | lane_bit(add_idx);
                dropped_q  <= dropped_q & ~lane_bit(add_idx);
                restore_ok <= lane_bit(add_idx);
                changed_q  <= 1'b1;
                if (change_cnt_q != 16'hFFFF)
                    change_cnt_q <= change_cnt_q + 1'b1;
            end else if (drop_idx >= 0) begin
                mask_q    <= mask_q & ~({{(LANES-1){1'b0}}, 1'b1} << drop_idx);
                dropped_q <= dropped_q | ({{(LANES-1){1'b0}}, 1'b1} << drop_idx);
                changed_q <= 1'b1;
                if (change_cnt_q != 16'hFFFF)
                    change_cnt_q <= change_cnt_q + 1'b1;
                // Dropping the last allowed lane means the link cannot run at
                // the designed width any more.
                if (width_q - 1'b1 <= LW'(MIN_LANES))
                    width_error_q <= 1'b1;
            end
        end
    end

endmodule
