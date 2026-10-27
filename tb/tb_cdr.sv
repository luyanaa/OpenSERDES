`timescale 1ns/1ps

// Fixed-rate CDR unit test.
//
// The data source runs at exactly OVERSAMPLE cycles per bit but is deliberately
// offset from the CDR clock phase.  The scoreboard compares the actual
// data_out/sample_strobe stream against the transmitted PRBS7 sequence after
// finding the one-bit startup offset.  It does not peek at internal CDR state.
module tb_cdr #(
    parameter int  OVERSAMPLE  = 4,
    parameter int  SYNC_STAGES = 3,
    parameter int  LOCK_THRESH = 8,
    parameter int  N_CHECK     = 200
);

    localparam int  N_BITS     = N_CHECK + 256;
    localparam real BIT_PERIOD = OVERSAMPLE * 10.0;

    logic clk;
    logic rst_n;
    logic enable;
    logic data_in;
    logic data_out;
    logic sample_strobe;
    logic clk_out;
    logic lock;
    logic lock_stale;

    logic [N_BITS-1:0] tx_bits;
    logic [N_CHECK-1:0] rx_bits;

    int errors;
    int samples;
    int offset;
    int best_errors;
    int errs;
    int lock_wait;

    cdr_oversampling #(
        .OVERSAMPLE(OVERSAMPLE),
        .SYNC_STAGES(SYNC_STAGES),
        .LOCK_THRESH(LOCK_THRESH),
        .LOCK_STALE_BITS(16)
    ) dut (
        .clk(clk),
        .rst_n(rst_n),
        .enable(enable),
        .flush(1'b0),
        .data_in(data_in),
        .data_out(data_out),
        .sample_strobe(sample_strobe),
        .clk_out(clk_out),
        .lock(lock),
        .lock_stale(lock_stale)
    );

    always #5 clk = ~clk;

    // PRBS7, deterministic.
    initial begin
        logic [6:0] state;
        state = 7'h7f;
        for (int i = 0; i < N_BITS; i++) begin
            tx_bits[i] = state[6];
            state = {state[5:0], state[6] ^ state[5]};
        end
    end

    // Transmit one bit per 4 CDR clocks, offset from the clock phase.
    initial begin
        data_in = 1'b0;
        wait (rst_n);
        #3.7;
        for (int i = 0; i < N_BITS; i++) begin
            data_in = tx_bits[i];
            #(BIT_PERIOD);
        end
    end

    // Collect the actual recovered stream after lock and settling.
    initial begin
        samples = 0;
        wait (rst_n);
        wait (lock);
        repeat (8) @(posedge clk);
        while (samples < N_CHECK) begin
            @(posedge clk);
            #1;
            if (sample_strobe) begin
                rx_bits[samples] = data_out;
                samples++;
            end
        end
    end

    initial begin
        clk       = 1'b0;
        rst_n     = 1'b0;
        enable    = 1'b0;
        errors    = 0;
        lock_wait = 0;

        repeat (3) @(posedge clk);
        rst_n = 1'b1;
        repeat (2) @(posedge clk);
        enable = 1'b1;

        while (!lock && lock_wait < 200) begin
            @(posedge clk);
            #1;
            lock_wait++;
        end
        if (!lock) begin
            $display("ERROR: CDR did not lock");
            $fatal(1);
        end

        wait (samples == N_CHECK);
        #1;

        // Find the one-bit startup offset using the first 64 samples.
        best_errors = N_CHECK + 1;
        offset      = 0;
        for (int off = 0; off < 64; off++) begin
            errs = 0;
            for (int k = 0; k < 64; k++) begin
                if (rx_bits[k] !== tx_bits[off + k])
                    errs++;
            end
            if (errs < best_errors) begin
                best_errors = errs;
                offset      = off;
            end
        end

        for (int k = 0; k < N_CHECK; k++) begin
            if (rx_bits[k] !== tx_bits[offset + k]) begin
                $display("ERROR: CDR bit %0d got %0b expected %0b (offset %0d)",
                         k, rx_bits[k], tx_bits[offset + k], offset);
                errors++;
            end
        end

        if (lock_stale) begin
            $display("ERROR: lock_stale asserted on PRBS data");
            errors++;
        end

        if (errors != 0) begin
            $display("FAIL: tb_cdr errors=%0d offset=%0d best_errors=%0d",
                     errors, offset, best_errors);
            $fatal(1);
        end
        $display("PASS: tb_cdr samples=%0d offset=%0d lock_wait=%0d",
                 samples, offset, lock_wait);
        $finish;
    end

endmodule
