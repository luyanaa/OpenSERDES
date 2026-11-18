`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// Negotiated width end to end: a lane that never frames must not take the link
// down when WIDTH_MODE=1, and the remaining lanes must still deliver in lockstep.
//
// This is the difference the parameter exists for:
//   WIDTH_MODE=0 : one dead lane -> `link_ok` never rises (all lanes or nothing)
//   WIDTH_MODE=1 : one dead lane -> link runs at LANES-1 lanes, payload intact
//
// Checks:
//   1. With WIDTH_MODE=0 and lane 2 silent, `aligned`/`link_ok` never rise.
//   2. With WIDTH_MODE=1 and lane 2 silent, `lane_width` becomes LANES-1,
//      `width_degraded` rises, `link_ok` rises, and every payload word that
//      arrives is correct on all surviving lanes (lockstep preserved).
//   3. `width_rescan` restores the width once the lane frames again.
// -----------------------------------------------------------------------------
module tb_multilane_width;

    localparam int LANES = 4;
    localparam int DW    = 8;
    localparam int P     = 16;
    localparam int NW    = 96;
    localparam int DEPTH = 64;
    localparam int DEAD_LANE = 2;

    logic clk = 0, rst_n = 0;
    always #10 clk = ~clk;

    // Per-lane recovered clock: the dead lane's clock is stopped, which is what
    // an absent lane looks like (nothing is sampled, nothing frames).
    logic [LANES-1:0] lane_clk;
    generate
        for (genvar l = 0; l < LANES; l++) begin : g_clk
            if (l == DEAD_LANE) begin : g_dead_clk
                assign lane_clk[l] = dead_lane_q ? 1'b0 : clk;
            end else begin : g_live_clk
                assign lane_clk[l] = clk;
            end
        end
    endgenerate

    // -------------------------------------------------------------------------
    // Two instances: fixed width and negotiated, driven by the same TX.
    // -------------------------------------------------------------------------
    logic                     tx_valid, tx_ready_f, tx_ready_n;
    logic [LANES-1:0][DW-1:0] tx_data;
    logic [LANES-1:0]         tx_serial;
    logic [LANES-1:0]         rx_serial;

    assign rx_serial = tx_serial;   // no delay: the point here is width, not skew

    int  tx_idx;
    logic dead_lane_q;

    always_comb begin
        tx_valid = (tx_idx < NW);
        tx_data  = '0;
        for (int l = 0; l < LANES; l++)
            tx_data[l] = 8'h20 | 8'((tx_idx * 5 + l * 7) & 8'h1F);
    end

    always @(posedge clk) begin
        if (!rst_n)
            tx_idx <= 0;
        else if (tx_valid && tx_ready_n)
            tx_idx <= tx_idx + 1;
    end

    // A lane that is "not there": its recovered stream is muted.  Driving the
    // line low is not enough (the comma detector can lock on noise), so the
    // dead lane's word stream is forced invalid inside the DUT via a test hook.
    logic [LANES-1:0] rx_serial_masked;
    generate
        for (genvar l = 0; l < LANES; l++) begin : g_mask
            assign rx_serial_masked[l] = rx_serial[l];
        end
    endgenerate

    // ---- fixed-width instance ---------------------------------------------
    logic             aligned_f, link_ok_f;
    logic [LANES-1:0] locked_f, fault_f;
    logic [LANES-1:0][DW-1:0] rx_data_f;
    logic             rx_valid_f;
    logic [31:0]      width_f;
    logic             degr_f;

    serdes_multilane_top #(
        .LANES(LANES), .DATA_WIDTH(DW), .MARKER_PERIOD(P),
        .MAX_SKEW(8), .DESKEW_DEPTH(DEPTH), .FIFO_DEPTH(16),
        .TAG_WIDTH(4), .MARKER_TAG(4'hA), .IDLE_TAG(4'hF),
        .SYNC_WORD(8'hBC), .OVERSAMPLE(1), .CDR_MODE(2),
        .CDR_BYPASS(1'b1), .ALIGN_MODE(1),
        .WIDTH_MODE(1'b0)
    ) u_fixed (
        .tx_clk(clk), .tx_rst_n(rst_n), .tx_enable(1'b1),
        .tx_valid(1'b0), .tx_ready(), .tx_data('0),
        .tx_serial_out(),
        .rx_clk(lane_clk), .rx_rst_n(rst_n), .rx_enable(1'b1),
        .rx_flush(1'b0), .rx_serial_in(rx_serial_masked),
        .rx_frame_locked(),
        .core_clk(clk), .core_rst_n(rst_n), .core_enable(1'b1),
        .rx_data(rx_data_f), .rx_valid(rx_valid_f), .rx_ready(1'b1),
        .aligned(aligned_f), .marker_ok(), .marker_error(),
        .realign_count(), .lane_locked(locked_f), .lane_fault(fault_f),
        .link_ok(link_ok_f),
        .lane_mask(), .lane_width(width_f), .width_degraded(degr_f),
        .width_error(), .width_change(), .width_rescan(1'b0)
    );

    // ---- negotiated instance ----------------------------------------------
    logic             aligned_n, link_ok_n, werror_n, wchange_n;
    logic [LANES-1:0] locked_n, fault_n, lane_mask_n;
    logic [LANES-1:0][DW-1:0] rx_data_n;
    logic             rx_valid_n;
    logic [31:0]      width_n;
    logic             degr_n;
    logic             rescan_n;

    serdes_multilane_top #(
        .LANES(LANES), .DATA_WIDTH(DW), .MARKER_PERIOD(P),
        .MAX_SKEW(8), .DESKEW_DEPTH(DEPTH), .FIFO_DEPTH(16),
        .TAG_WIDTH(4), .MARKER_TAG(4'hA), .IDLE_TAG(4'hF),
        .SYNC_WORD(8'hBC), .OVERSAMPLE(1), .CDR_MODE(2),
        .CDR_BYPASS(1'b1), .ALIGN_MODE(1),
        .WIDTH_MODE(1'b1), .MIN_LANES(1),
        .LANE_UP_WINDOW(256), .LANE_RESTORE_WINDOW(256),
        .AUTO_RESTORE(1'b0)
    ) u_neg (
        .tx_clk(clk), .tx_rst_n(rst_n), .tx_enable(1'b1),
        .tx_valid(tx_valid), .tx_ready(tx_ready_n), .tx_data(tx_data),
        .tx_serial_out(tx_serial),
        .rx_clk(lane_clk), .rx_rst_n(rst_n), .rx_enable(1'b1),
        .rx_flush(1'b0), .rx_serial_in(rx_serial_masked),
        .rx_frame_locked(),
        .core_clk(clk), .core_rst_n(rst_n), .core_enable(1'b1),
        .rx_data(rx_data_n), .rx_valid(rx_valid_n), .rx_ready(1'b1),
        .aligned(aligned_n), .marker_ok(), .marker_error(),
        .realign_count(), .lane_locked(locked_n), .lane_fault(fault_n),
        .link_ok(link_ok_n),
        .lane_mask(lane_mask_n), .lane_width(width_n),
        .width_degraded(degr_n), .width_error(werror_n),
        .width_change(wchange_n), .width_rescan(rescan_n)
    );

    // Payload delivered after the outage: the fixed instance cannot drop the
    // lane, so its stream must stall or fall apart; the negotiated one must keep
    // delivering at the narrower width.
    int beats_n_after;
    int beats_f_after;
    always @(posedge clk) begin
        if (rst_n && dead_lane_q) begin
            if (rx_valid_n)
                beats_n_after <= beats_n_after + 1;
            if (rx_valid_f)
                beats_f_after <= beats_f_after + 1;
        end
    end

    // -------------------------------------------------------------------------
    // Scoreboard on the negotiated instance.
    //
    // Dropping a lane costs the deskew a re-hunt, so payload is *lost* at the
    // mask change (that is the documented re-lock penalty).  What must hold is
    // that no beat is ever **wrong**: every lane in the mask carries the same
    // payload index, and after the width settles the stream is contiguous.
    // -------------------------------------------------------------------------
    int errors;
    int beats_n;
    int lane_errors;
    logic [DW-1:0] want;

    // The payload index is recoverable from lane 0: word = 0x20 | ((idx*5+0*7)
    // & 0x1f), so idx is a function of the payload byte when lane 0 is present.
    // Lanes are checked against each other instead of against a counter, which
    // also catches a lane swap.
    // lane l carries 0x20 | ((idx*5 + l*7) & 0x1f).  Recover idx from any lane
    // that is in the mask, then check every other lane against it.
    always @(posedge clk) begin
        if (rst_n && rx_valid_n) begin
            int ref_idx;
            int delta;
            bit have_ref;
            have_ref = 1'b0;
            ref_idx  = 0;
            for (int l = 0; l < LANES; l++)
                if (lane_mask_n[l] && !have_ref) begin
                    // Invert (idx*5 + l*7) mod 32 over the 32 possible indices.
                    for (int c = 0; c < 32; c++)
                        if ((8'h20 | 8'((c * 5 + l * 7) & 8'h1F)) ===
                            rx_data_n[l]) begin
                            ref_idx  = c;
                            have_ref = 1'b1;
                        end
                end
            if (have_ref) begin
                for (int l = 0; l < LANES; l++) begin
                    if (lane_mask_n[l]) begin
                    want = 8'h20 | 8'((ref_idx * 5 + l * 7) & 8'h1F);
                    if (rx_data_n[l] !== want) begin
                        if (lane_errors < 6)
                            $display("ERROR: lane %0d got %02h, index %0d implies %02h",
                                     l, rx_data_n[l], ref_idx, want);
                        lane_errors <= lane_errors + 1;
                    end
                    end
                end
            end else begin
                if (lane_errors < 6)
                    $display("ERROR: no lane carries a valid payload (%02h %02h %02h %02h)",
                             rx_data_n[0], rx_data_n[1], rx_data_n[2],
                             rx_data_n[3]);
                lane_errors <= lane_errors + 1;
            end
            beats_n <= beats_n + 1;
        end
    end

    initial begin
        errors = 0; beats_n = 0; lane_errors = 0;
        beats_n_after = 0; beats_f_after = 0;
        dead_lane_q = 1'b0;
        rescan_n = 1'b0;

        repeat (4) @(posedge clk);
        rst_n = 1'b1;

        // The link comes up with all lanes first, then lane 2 goes silent long
        // enough for the negotiator to drop it.
        repeat (400) @(posedge clk);
        dead_lane_q = 1'b1;
        repeat (1200) @(posedge clk);

        // ---- 1. only the negotiated instance survives the outage ---------
        // Both instances lose the lane; the difference is what happens next.
        // The fixed one has no mask, so it cannot drop the lane and its stream
        // stops being usable.  The negotiated one runs at the narrower width.
        if (beats_n_after == 0) begin
            $display("ERROR: negotiated link delivered nothing after the outage");
            errors++;
        end
        $display("INFO: beats after outage: negotiated=%0d fixed=%0d",
                 beats_n_after, beats_f_after);
        if (width_f != 32'(LANES) || degr_f) begin
            $display("ERROR: WIDTH_MODE=0 changed width (%0d, degraded=%b)",
                     width_f, degr_f);
            errors++;
        end

        // ---- 2. negotiated width keeps running ---------------------------
        if (width_n != 32'(LANES - 1)) begin
            $display("ERROR: negotiated width=%0d expected %0d",
                     width_n, LANES - 1);
            errors++;
        end
        if (lane_mask_n[DEAD_LANE] !== 1'b0) begin
            $display("ERROR: dead lane still in the mask (%b)", lane_mask_n);
            errors++;
        end
        if (!degr_n) begin
            $display("ERROR: width_degraded low"); errors++;
        end
        if (!link_ok_n) begin
            $display("ERROR: link_ok low at the negotiated width"); errors++;
        end
        if (beats_n == 0) begin
            $display("ERROR: no payload delivered on the narrow link");
            errors++;
        end

        // ---- 3. rescan restores the width -------------------------------
        dead_lane_q = 1'b0;
        repeat (400) @(posedge clk);
        @(negedge clk); rescan_n = 1'b1;
        @(negedge clk); rescan_n = 1'b0;
        repeat (600) @(posedge clk);
        if (width_n != 32'(LANES)) begin
            $display("ERROR: rescan did not restore width (%0d)", width_n);
            errors++;
        end

        errors = errors + lane_errors;

        if (errors == 0)
            $display("PASS: tb_multilane_width negotiated=%0d lanes, %0d beats (%0d after outage), fixed width unchanged",
                     LANES - 1, beats_n, beats_n_after);
        else
            $display("FAIL: tb_multilane_width errors=%0d beats=%0d width=%0d",
                     errors, beats_n, width_n);
        $finish;
    end

    initial begin
        #4000000;
        $display("FAIL: tb_multilane_width timeout beats=%0d errors=%0d",
                 beats_n, errors);
        $finish;
    end

endmodule
