`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// Lane width negotiation.
//
// WIDTH_MODE=0 must be *exactly* the old behaviour: a constant all-ones mask,
// no degradation, no drops.  WIDTH_MODE=1 must drop a lane that never frames,
// publish the narrower width, keep the link usable, and never go below
// MIN_LANES (latching `width_error` instead).
//
// Checks:
//   1. WIDTH_MODE=0: a dead lane does not narrow the link (mask stays all ones,
//      degraded=0), i.e. the old "all lanes or nothing" contract is untouched.
//   2. WIDTH_MODE=1: a dead lane is dropped after LANE_UP_WINDOW, `width` drops
//      to LANES-1, `degraded` rises, `width_change` pulses once.
//   3. Dropping a second lane is allowed down to MIN_LANES; below that
//      `width_error` latches and the mask stops shrinking.
//   4. `rescan` restores every lane and clears `width_error`.
//   5. AUTO_RESTORE=0: a recovered lane does not come back on its own;
//      AUTO_RESTORE=1: it does after LANE_RESTORE_WINDOW.
//   6. A lane that frames late (still inside the window) is never dropped.
// -----------------------------------------------------------------------------
module tb_lane_width;

    localparam int LANES = 4;
    localparam int UP_W  = 32;
    localparam int RS_W  = 64;

    logic clk = 0, rst_n = 0, enable = 0;
    always #5 clk = ~clk;

    int errors;

    logic [LANES-1:0] lane_ok;
    logic             clear, rescan;

    logic [LANES-1:0] mask, dropped, restored;
    logic [31:0]      width;
    logic             degraded, change, werror;
    logic [15:0]      change_count;

    /* verilator lint_off PINCONNECTEMPTY */
    lane_width_negotiator #(
        .LANES(LANES), .MIN_LANES(2),
        .LANE_UP_WINDOW(UP_W), .LANE_RESTORE_WINDOW(RS_W),
        .AUTO_RESTORE(1'b0), .WIDTH_MODE(1'b1)
    ) u_neg (
        .clk(clk), .rst_n(rst_n), .enable(enable), .clear(clear),
        .rescan(rescan), .lane_ok(lane_ok),
        .lane_mask(mask), .lane_dropped(dropped), .lane_restored(restored),
        .width(width), .degraded(degraded), .width_change(change),
        .width_error(werror), .width_change_count(change_count)
    );

    // Same DUT in the fixed-width configuration.
    logic [LANES-1:0] mask_fixed;
    logic [31:0]      width_fixed;
    logic             degraded_fixed, werror_fixed;

    lane_width_negotiator #(
        .LANES(LANES), .MIN_LANES(2),
        .LANE_UP_WINDOW(UP_W), .LANE_RESTORE_WINDOW(RS_W),
        .AUTO_RESTORE(1'b0), .WIDTH_MODE(1'b0)
    ) u_fixed (
        .clk(clk), .rst_n(rst_n), .enable(enable), .clear(clear),
        .rescan(rescan), .lane_ok(lane_ok),
        .lane_mask(mask_fixed), .lane_dropped(), .lane_restored(),
        .width(width_fixed), .degraded(degraded_fixed),
        .width_change(), .width_error(werror_fixed),
        .width_change_count()
    );

    // AUTO_RESTORE=1 variant.
    logic [LANES-1:0] mask_ar;
    logic [31:0]      width_ar;
    logic             change_ar;

    lane_width_negotiator #(
        .LANES(LANES), .MIN_LANES(2),
        .LANE_UP_WINDOW(UP_W), .LANE_RESTORE_WINDOW(RS_W),
        .AUTO_RESTORE(1'b1), .WIDTH_MODE(1'b1)
    ) u_auto (
        .clk(clk), .rst_n(rst_n), .enable(enable), .clear(clear),
        .rescan(1'b0), .lane_ok(lane_ok),
        .lane_mask(mask_ar), .lane_dropped(), .lane_restored(),
        .width(width_ar), .degraded(), .width_change(change_ar),
        .width_error(), .width_change_count()
    );
    /* verilator lint_on PINCONNECTEMPTY */

    int changes;
    always @(posedge clk)
        if (rst_n && enable && change)
            changes <= changes + 1;

    initial begin
        errors = 0; changes = 0;
        lane_ok = '1;
        clear = 0; rescan = 0;

        repeat (3) @(posedge clk);
        rst_n  = 1'b1;
        enable = 1'b1;
        repeat (4) @(posedge clk);

        // ---- 1. fixed width ignores a dead lane ---------------------------
        lane_ok[2] = 1'b0;
        repeat (4 * UP_W) @(posedge clk);
        if (mask_fixed !== {LANES{1'b1}} || degraded_fixed || werror_fixed) begin
            $display("ERROR: WIDTH_MODE=0 degraded (mask=%b width=%0d)",
                     mask_fixed, width_fixed);
            errors++;
        end
        if (width_fixed != 32'(LANES)) begin
            $display("ERROR: WIDTH_MODE=0 width=%0d", width_fixed);
            errors++;
        end

        // ---- 2. negotiated width drops it --------------------------------
        if (mask[2] !== 1'b0) begin
            $display("ERROR: dead lane 2 not dropped (mask=%b)", mask);
            errors++;
        end
        if (width != 32'(LANES - 1) || !degraded || dropped[2] !== 1'b1) begin
            $display("ERROR: width=%0d degraded=%b dropped=%b",
                     width, degraded, dropped);
            errors++;
        end
        $display("INFO: dropped lane 2 -> width %0d (changes=%0d)",
                 width, changes);

        // ---- 6. later lanes dropped in turn, down to MIN_LANES ------------
        // Dropping lane 3 takes the link to MIN_LANES, which is allowed but is
        // the floor: `width_error` latches there, because the link cannot lose
        // another lane without the protocol reporting a lower capability.
        lane_ok[3] = 1'b0;
        repeat (4 * UP_W) @(posedge clk);
        if (mask !== 4'b0011 || width != 32'd2) begin
            $display("ERROR: second drop: mask=%b width=%0d", mask, width);
            errors++;
        end
        if (!werror) begin
            $display("ERROR: width_error did not latch at MIN_LANES");
            errors++;
        end
        // Lane 0 now dies: the mask must not shrink below MIN_LANES.
        lane_ok[0] = 1'b0;
        repeat (4 * UP_W) @(posedge clk);
        if (width < 32'd2) begin
            $display("ERROR: width %0d below MIN_LANES=2", width);
            errors++;
        end
        if (mask[0] !== 1'b1) begin
            $display("ERROR: mask shrank below the floor (mask=%b)", mask);
            errors++;
        end
        $display("INFO: floor reached -> width %0d error=%b", width, werror);

        // ---- 4. rescan restores ------------------------------------------
        lane_ok = '1;
        @(negedge clk); rescan = 1'b1;
        @(negedge clk); rescan = 1'b0;
        @(posedge clk); #1;
        if (mask !== {LANES{1'b1}} || werror) begin
            $display("ERROR: rescan did not restore (mask=%b err=%b)",
                     mask, werror);
            errors++;
        end
        if (width != 32'(LANES)) begin
            $display("ERROR: width after rescan=%0d", width);
            errors++;
        end

        // ---- 5. AUTO_RESTORE=1 re-adds a recovered lane ------------------
        // Lane 2 was dead for the whole run, so u_auto dropped it; make it
        // good and wait out its restore window.
        lane_ok  = '1;
        lane_ok[2] = 1'b1;
        repeat (2 * RS_W) @(posedge clk);
        if (mask_ar !== {LANES{1'b1}}) begin
            $display("ERROR: AUTO_RESTORE did not restore lane 2 (mask=%b)",
                     mask_ar);
            errors++;
        end
        if (width_ar != 32'(LANES)) begin
            $display("ERROR: AUTO_RESTORE width=%0d", width_ar);
            errors++;
        end

        if (errors == 0)
            $display("PASS: tb_lane_width fixed-mode untouched, negotiated drop/floor/rescan ok");
        else
            $display("FAIL: tb_lane_width errors=%0d", errors);
        $finish;
    end

    initial begin
        #2000000;
        $display("FAIL: tb_lane_width timeout errors=%0d", errors);
        $finish;
    end

endmodule
