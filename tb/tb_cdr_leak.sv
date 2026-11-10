`timescale 1ns/1ps
// Directed test for the integral-path leak period.
//
// LEAK_UI_BITS is documented as a power of two: the leak removes one LSB
// after 2^LEAK_UI_BITS *consecutive* UIs with no transition.  An earlier
// implementation compared the counter against LEAK_UI_BITS itself, so
// LEAK_UI_BITS=12 leaked every 13 UIs instead of every 4096.
//
// The test is deterministic: it seeds freq_q to a known value and holds the
// line constant, so no vote and no kick ever touch the integral path and the
// only force on it is the leak.  The interval between freq_q steps must be
// exactly 2^LEAK_UI_BITS UIs (and a negative LEAK_UI_BITS must never step).
module tb_cdr_leak #(
    parameter int LEAK_UI_BITS = 3,
    parameter int OVERSAMPLE   = 4,
    parameter int SEED         = 64,
    parameter int N_CHANGES    = 8,
    parameter int DISABLED_UIS = 512   // quiet window when the leak is off
);
    localparam bit LEAK_ON  = (LEAK_UI_BITS >= 0);
    localparam int PERIOD   = LEAK_ON ? (1 << LEAK_UI_BITS) : 0;

    logic clk, rst_n, enable, data_in;
    logic data_out, strobe, lock, lock_stale;
    logic signed [12:0] freq_trim;
    int   ui_cnt;
    int   changes;
    int   last_ui;
    int   last_fq;
    int   fail;
    bit   seeded;
    bit   armed;

    cdr_bang_bang #(
        .OVERSAMPLE(OVERSAMPLE), .SYNC_STAGES(3),
        .KI(1), .KP_SHIFT(5), .LEAK_UI_BITS(LEAK_UI_BITS),
        .LOCK_EDGES(16), .LOCK_STALE_BITS(16),
        .PHASE_CTRL_W(2), .FRAC_BITS(12)
    ) dut (
        .clk(clk), .rst_n(rst_n), .enable(enable), .flush(1'b0),
        .data_in(data_in), .phase_offset('0), .data_out(data_out),
        .sample_strobe(strobe), .clk_out(), .lock(lock),
        .lock_stale(lock_stale), .freq_trim(freq_trim)
    );

    always #5 clk = ~clk;

    always @(posedge clk) if (dut.wrap) ui_cnt <= ui_cnt + 1;

    // Constant line: the PD never votes, so freq_q only moves via the leak.
    initial begin
        data_in = 1'b0;
    end

    initial begin
        clk = 1'b0; rst_n = 1'b0; enable = 1'b0;
        ui_cnt = 0; changes = 0; last_ui = -1; fail = 0; seeded = 1'b0;
        armed = 1'b0;
        last_fq = 0;
        repeat (3) @(posedge clk); rst_n = 1'b1;
        repeat (2) @(posedge clk); enable = 1'b1;
        // Seed the integral once, off the clock edge so nothing races it.
        repeat (8) @(posedge clk);
        @(negedge clk);
        dut.freq_q = -SEED;
        seeded = 1'b1;
    end

    always @(negedge clk) begin
        if (!seeded) begin
            last_fq = int'(dut.freq_q);
        end else if (!armed) begin
            // One negedge after the seed: take the seeded value as the
            // baseline so the seeding itself is never counted as a leak step.
            last_fq = int'(dut.freq_q);
            last_ui = ui_cnt;
            armed   = 1'b1;
        end else if (dut.freq_q != last_fq) begin
            // The counter holds a residue when the test starts, so the first
            // interval is partial and only seeds the measurement; every later
            // interval must be exactly 2^LEAK_UI_BITS UIs.
            if (changes >= 1) begin
                int gap;
                gap = ui_cnt - last_ui;
                if (gap != PERIOD) begin
                    $display("FAIL: leak interval %0d UI, expected %0d (LEAK_UI_BITS=%0d)",
                             gap, PERIOD, LEAK_UI_BITS);
                    fail = 1;
                end else begin
                    $display("INFO: leak interval %0d UI (expected %0d, freq_trim=%0d)",
                             gap, PERIOD, freq_trim);
                end
            end
            if (last_fq != 0 &&
                (($signed(dut.freq_q) > 0 && $signed(dut.freq_q) > last_fq) ||
                 ($signed(dut.freq_q) < 0 && $signed(dut.freq_q) < last_fq))) begin
                $display("FAIL: leak moved freq_q away from zero (%0d -> %0d)",
                         last_fq, dut.freq_q);
                fail = 1;
            end
            last_ui = ui_cnt;
            last_fq = int'(dut.freq_q);
            changes++;
        end

        if (LEAK_ON && changes >= N_CHANGES + 2) begin
            if (fail) $fatal(1, "tb_cdr_leak failed");
            $display("PASS: tb_cdr_leak LEAK_UI_BITS=%0d period=%0d UI",
                     LEAK_UI_BITS, PERIOD);
            $finish;
        end

        // Disabled leak: freq_q must not move at all.
        if (!LEAK_ON && armed && ui_cnt > DISABLED_UIS) begin
            if (changes != 0) begin
                $display("FAIL: leak disabled but freq_q moved %0d times", changes);
                $fatal(1, "tb_cdr_leak failed");
            end
            $display("PASS: tb_cdr_leak LEAK_UI_BITS=%0d (disabled, freq_q held)",
                     LEAK_UI_BITS);
            $finish;
        end
    end
endmodule
