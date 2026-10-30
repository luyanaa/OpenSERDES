`timescale 1ns/1ps

module tb_link_training_fsm;
    localparam int PHASE_STEPS  = 8;
    localparam int THRESH_STEPS = 4;
    localparam int DWELL        = 8;
    localparam int BEST_PHASE   = 3;
    localparam int BEST_THRESH  = 2;

    logic clk = 1'b0;
    logic rst_n = 1'b0;
    logic enable = 1'b1;
    logic start = 1'b0;
    logic [15:0] error_count = '0;
    logic [2:0] state;
    logic [2:0] phase_sel;
    logic [1:0] thresh_sel;
    logic phase_we;
    logic thresh_we;
    logic busy;
    logic locked;
    logic retrain;
    logic [2:0] best_phase;
    logic [1:0] best_thresh;
    logic [15:0] best_errors;

    logic inject_error = 1'b0;
    logic err_inc;
    logic saw_retrain = 1'b0;
    int errors_seen = 0;

    always #5 clk = ~clk;

    link_training_fsm #(
        .PHASE_STEPS(PHASE_STEPS),
        .THRESH_STEPS(THRESH_STEPS),
        .DWELL_CYCLES(DWELL),
        .ERROR_WIDTH(16),
        .RETRAIN_ERR_THRESH(2),
        .RETRAIN_WINDOW(16)
    ) dut (
        .clk(clk), .rst_n(rst_n), .enable(enable), .start(start),
        .error_count(error_count),
        .phase_sel(phase_sel), .thresh_sel(thresh_sel),
        .phase_we(phase_we), .thresh_we(thresh_we),
        .busy(busy), .locked(locked), .retrain(retrain), .state(state),
        .best_phase(best_phase), .best_thresh(best_thresh),
        .best_errors(best_errors)
    );

    // Synthetic error source: every cycle spent away from the best candidate
    // increments the error counter.  This is the same contract as a PRBS
    // checker's monotonic error count.
    assign err_inc = (state == 3'd1 && phase_sel != BEST_PHASE) ||
                     (state == 3'd2 && thresh_sel != BEST_THRESH) ||
                     (state == 3'd3 && inject_error);

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            error_count <= '0;
            saw_retrain <= 1'b0;
        end else if (err_inc) begin
            error_count <= error_count + 1'b1;
            errors_seen <= errors_seen + 1;
        end
        if (retrain)
            saw_retrain <= 1'b1;
    end

    task automatic pulse_start;
        begin
            @(negedge clk);
            start = 1'b1;
            @(negedge clk);
            start = 1'b0;
        end
    endtask

    task automatic wait_locked(input string label, input int timeout);
        int n;
        begin
            n = 0;
            while (!locked && n < timeout) begin
                @(posedge clk);
                n = n + 1;
            end
            if (!locked) begin
                $display("FAIL %s: timeout state=%0d phase=%0d thresh=%0d",
                         label, state, phase_sel, thresh_sel);
                $finish;
            end
        end
    endtask

    initial begin
        repeat (3) @(posedge clk);
        rst_n = 1'b1;
        repeat (2) @(posedge clk);
        pulse_start();
        wait_locked("initial scan", 400);
        if (best_phase != BEST_PHASE) begin
            $display("FAIL best_phase=%0d expected=%0d", best_phase, BEST_PHASE);
            $finish;
        end
        if (best_thresh != BEST_THRESH) begin
            $display("FAIL best_thresh=%0d expected=%0d", best_thresh, BEST_THRESH);
            $finish;
        end
        $display("PASS initial scan best_phase=%0d best_thresh=%0d errors=%0d",
                 best_phase, best_thresh, best_errors);

        // Inject a sustained error increase while locked; the monitor must
        // request a retrain and drop locked.
        inject_error = 1'b1;
        repeat (20) @(posedge clk);
        if (!saw_retrain || locked) begin
            $display("FAIL retrain did not assert or locked stayed high: retrain=%0d locked=%0d",
                     saw_retrain, locked);
            $finish;
        end
        inject_error = 1'b0;
        wait_locked("retrain scan", 400);
        $display("PASS retrain recovered locked=%0d best_phase=%0d best_thresh=%0d",
                 locked, best_phase, best_thresh);
        $finish;
    end

    initial begin
        #5000;
        $display("FAIL timeout");
        $finish;
    end

endmodule
