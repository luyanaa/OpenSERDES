`timescale 1ns/1ps

module tb_cdr;

    localparam int OVERSAMPLE  = 4;
    localparam int SYNC_STAGES = 2;
    localparam int LOCK_THRESH = 8;

    logic clk;
    logic rst_n;
    logic enable;
    logic data_in;
    logic data_out;
    logic sample_strobe;
    logic clk_out;
    logic lock;

    int errors;
    int clk_cnt;
    int bit_cnt;
    int samples;
    int lock_wait;
    logic expected_sample;

    always @(posedge clk) begin
        if (rst_n && enable && dut.sample_now)
            expected_sample <= data_in;
    end

    cdr_oversampling #(
        .OVERSAMPLE(OVERSAMPLE),
        .SYNC_STAGES(SYNC_STAGES),
        .LOCK_THRESH(LOCK_THRESH),
        .LOCK_DECAY(1)
    ) dut (
        .clk(clk),
        .rst_n(rst_n),
        .enable(enable),
        .data_in(data_in),
        .data_out(data_out),
        .sample_strobe(sample_strobe),
        .clk_out(clk_out),
        .lock(lock)
    );

    always #5 clk = ~clk;

    // Data changes every OVERSAMPLE cycles, deliberately offset from phase 0
    // so the CDR has to align its sampling phase.
    always @(posedge clk) begin
        if (!rst_n) begin
            clk_cnt  <= 0;
            bit_cnt  <= 0;
            data_in  <= 1'b0;
        end else if (enable) begin
            clk_cnt <= clk_cnt + 1;
            if (clk_cnt % OVERSAMPLE == 1) begin
                data_in <= ~data_in;
                bit_cnt <= bit_cnt + 1;
            end
        end
    end

    // Check every recovered sample after lock.
    always @(negedge clk) begin
        if (rst_n && enable && sample_strobe) begin
            samples <= samples + 1;
            if (lock && data_out !== expected_sample) begin
                $display("ERROR: CDR sample mismatch: data_out=%0b expected=%0b",
                         data_out, expected_sample);
                errors <= errors + 1;
            end
        end
    end

    initial begin
        clk      = 1'b0;
        rst_n    = 1'b0;
        enable   = 1'b0;
        data_in  = 1'b0;
        errors   = 0;
        clk_cnt  = 0;
        bit_cnt  = 0;
        samples  = 0;
        lock_wait = 0;

        repeat (3) @(posedge clk);
        rst_n = 1'b1;
        repeat (2) @(posedge clk);
        enable = 1'b1;

        // Wait for lock, with a bounded timeout.
        while (!lock && lock_wait < 100) begin
            @(posedge clk);
            #1;
            lock_wait++;
        end
        if (!lock) begin
            $display("ERROR: CDR did not lock");
            $fatal(1);
        end

        // Collect a number of recovered samples.
        while (samples < 100) begin
            @(posedge clk);
            #1;
            if (lock_wait > 1000) begin
                $display("ERROR: timeout waiting for samples");
                $fatal(1);
            end
            lock_wait++;
        end

        enable = 1'b0;
        if (errors != 0) begin
            $display("FAIL: tb_cdr errors=%0d", errors);
            $fatal(1);
        end
        if (samples < 100) begin
            $display("FAIL: tb_cdr only %0d samples", samples);
            $fatal(1);
        end
        $display("PASS: tb_cdr samples=%0d lock_wait=%0d", samples, lock_wait);
        $finish;
    end

endmodule
