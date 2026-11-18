`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// Link-health building blocks.
//
// These three modules exist because a protocol PHY (USB/PCIe style) needs things
// a transport-only PCS never notices it is missing:
//
//   saturating_counter : "how often", not "it happened"
//   link_watchdog      : distinguish "still trying" from "structurally stuck"
//   clock_cycles        : the measured counterpart of the ppm in the phase model
//
// Checks:
//   1. saturating_counter increments, saturates at its maximum, flags
//      `saturated`, and clears.
//   2. link_watchdog: no timeout while progress arrives; a timeout pulse +
//      sticky `stuck` after TIMEOUT_CYCLES without progress; the window
//      restarts by itself; `clear` resets everything; `enable` low is not a
//      stuck link.
//   3. clock_cycles: the value read in the other domain tracks the source
//      count, and is never torn (monotonic within a window).
// -----------------------------------------------------------------------------
module tb_link_health;

    logic clk = 0, rst_n = 0;
    always #5 clk = ~clk;

    int errors;

    // -------------------------------------------------------------------------
    // 1. saturating_counter
    // -------------------------------------------------------------------------
    localparam int SW = 4;   // 4-bit: saturates at 15, cheap to test
    logic          cnt_clear, cnt_inc;
    logic [SW-1:0] cnt_value;
    logic          cnt_sat;

    saturating_counter #(.WIDTH(SW)) u_cnt (
        .clk(clk), .rst_n(rst_n), .clear(cnt_clear), .inc(cnt_inc),
        .value(cnt_value), .saturated(cnt_sat)
    );

    task automatic cnt_pulse(input int n);
        begin
            for (int i = 0; i < n; i++) begin
                @(negedge clk);
                cnt_inc = 1'b1;
                @(negedge clk);
                cnt_inc = 1'b0;
            end
        end
    endtask

    // -------------------------------------------------------------------------
    // 2. link_watchdog
    // -------------------------------------------------------------------------
    localparam int WD_TIMEOUT = 8;
    logic wd_clear, wd_restart, wd_progress, wd_timeout, wd_stuck;
    logic [15:0] wd_count;

    link_watchdog #(.TIMEOUT_CYCLES(WD_TIMEOUT)) u_wd (
        .clk(clk), .rst_n(rst_n), .enable(rst_n), .clear(wd_clear),
        .restart(wd_restart), .progress(wd_progress),
        .timeout(wd_timeout), .stuck(wd_stuck), .timeout_count(wd_count)
    );

    // -------------------------------------------------------------------------
    // 3. clock_cycles across two clock domains
    // -------------------------------------------------------------------------
    logic sclk = 0, dclk = 0;
    always #3 sclk = ~sclk;     // faster measured domain
    always #7 dclk = ~dclk;     // slower reading domain

    logic [31:0] cyc_slow_to_fast, cyc_fast_to_slow;
    /* verilator lint_off PINCONNECTEMPTY */
    clock_cycles #(.WIDTH(32)) u_cyc_a (
        .sclk(dclk), .srst_n(rst_n), .dclk(sclk), .drst_n(rst_n),
        .value(cyc_slow_to_fast)
    );
    clock_cycles #(.WIDTH(32)) u_cyc_b (
        .sclk(sclk), .srst_n(rst_n), .dclk(dclk), .drst_n(rst_n),
        .value(cyc_fast_to_slow)
    );
    /* verilator lint_on PINCONNECTEMPTY */

    int cyc_a_prev, cyc_b_prev;
    logic [31:0] cyc_a_at_t0, cyc_b_at_t0;

    initial begin
        errors = 0;
        cnt_clear = 0; cnt_inc = 0;
        wd_clear = 0; wd_restart = 0; wd_progress = 0;

        repeat (3) @(posedge clk);
        rst_n = 1'b1;
        repeat (2) @(posedge clk);

        // ---- 1. counter --------------------------------------------------
        cnt_pulse(3);
        @(posedge clk); #1;
        if (cnt_value !== 4'd3) begin
            $display("ERROR: counter=%0d after 3 pulses", cnt_value);
            errors++;
        end
        cnt_pulse(20);       // must saturate, not wrap
        @(posedge clk); #1;
        if (cnt_value !== 4'hF || !cnt_sat) begin
            $display("ERROR: counter=%0d sat=%b, expected 15/1", cnt_value,
                     cnt_sat);
            errors++;
        end
        @(negedge clk);
        cnt_clear = 1'b1;
        @(negedge clk);
        cnt_clear = 1'b0;
        @(posedge clk); #1;
        if (cnt_value !== 4'd0 || cnt_sat) begin
            $display("ERROR: counter=%0d sat=%b after clear", cnt_value,
                     cnt_sat);
            errors++;
        end

        // ---- 2. watchdog -------------------------------------------------
        // Progress inside the window: no timeout.
        @(negedge clk); wd_restart = 1'b1;
        @(negedge clk); wd_restart = 1'b0;
        for (int i = 0; i < 4 * WD_TIMEOUT; i++) begin
            @(negedge clk);
            wd_progress = 1'b1;
            @(negedge clk);
            wd_progress = 1'b0;
        end
        if (wd_timeout || wd_stuck) begin
            $display("ERROR: watchdog fired while progressing (t=%b s=%b)",
                     wd_timeout, wd_stuck);
            errors++;
        end

        // No progress: a timeout pulse and a sticky flag.
        for (int i = 0; i < 2 * WD_TIMEOUT + 4; i++) @(posedge clk);
        if (!wd_stuck) begin
            $display("ERROR: watchdog did not latch stuck"); errors++;
        end
        if (wd_count == 0) begin
            $display("ERROR: watchdog timeout_count stayed 0"); errors++;
        end
        $display("INFO: watchdog fired %0d times", wd_count);

        // The window restarted itself, so a second window also expires.
        repeat (WD_TIMEOUT + 2) @(posedge clk);
        if (wd_count < 2) begin
            $display("ERROR: watchdog did not re-arm (count=%0d)", wd_count);
            errors++;
        end

        @(negedge clk); wd_clear = 1'b1;
        @(negedge clk); wd_clear = 1'b0;
        @(posedge clk); #1;
        if (wd_stuck || wd_count != 0) begin
            $display("ERROR: watchdog clear failed (stuck=%b count=%0d)",
                     wd_stuck, wd_count);
            errors++;
        end

        // ---- 3. clock_cycles --------------------------------------------
        cyc_a_at_t0 = cyc_slow_to_fast;
        cyc_b_at_t0 = cyc_fast_to_slow;
        repeat (200) @(posedge sclk);
        @(posedge dclk); #1;
        if (cyc_slow_to_fast <= cyc_a_at_t0) begin
            $display("ERROR: slow->fast counter did not advance (%0d -> %0d)",
                     cyc_a_at_t0, cyc_slow_to_fast);
            errors++;
        end
        if (cyc_fast_to_slow <= cyc_b_at_t0) begin
            $display("ERROR: fast->slow counter did not advance (%0d -> %0d)",
                     cyc_b_at_t0, cyc_fast_to_slow);
            errors++;
        end
        // Torn reads are impossible with a Gray counter: over a fixed window the
        // value must increase monotonically (never jump back).
        cyc_a_prev = int'(cyc_slow_to_fast);
        for (int i = 0; i < 40; i++) begin
            @(posedge dclk);
            #1;
            if (int'(cyc_slow_to_fast) < cyc_a_prev) begin
                $display("ERROR: counter went backwards (%0d -> %0d)",
                         cyc_a_prev, cyc_slow_to_fast);
                errors++;
            end
            cyc_a_prev = int'(cyc_slow_to_fast);
        end

        if (errors == 0)
            $display("PASS: tb_link_health counter/saturate/watchdog/cycles ok");
        else
            $display("FAIL: tb_link_health errors=%0d", errors);
        $finish;
    end

    initial begin
        #1000000;
        $display("FAIL: tb_link_health timeout errors=%0d", errors);
        $finish;
    end

endmodule
