`timescale 1ns/1ps

// Adaptive CDR test with a deliberate TX/RX frequency offset.
//
// The data source runs at BIT_PERIOD = OS * 10 ns * (1 + PPM/1e6), while the
// CDR clock is exactly 100 MHz.  The test collects 1500 recovered bits after
// lock, finds the startup bit offset, and requires every bit to match.  At
// 700 ppm this spans more than one UI of accumulated drift, so a fixed-phase
// CDR would slip.
module tb_cdr_adaptive #(
    parameter real PPM      = 700.0,
    parameter int  N_CHECK  = 1500
);

    localparam int  OS         = 8;
    localparam int  SYNC       = 3;
    localparam int  LOCK_TH    = 8;
    localparam int  N_BITS     = N_CHECK + 256;
    localparam real BIT_PERIOD = 80.0 * (1.0 + PPM / 1000000.0);

    logic clk;
    logic rst_n;
    logic enable;
    logic data_in;
    logic data_out;
    logic sample_strobe;
    logic clk_out;
    logic lock;
    logic lock_stale;
    logic signed [8:0] freq_trim;

    logic [N_BITS-1:0]  tx_bits;
    logic [N_CHECK-1:0] rx_bits;

    int errors;
    int samples;
    int offset;
    int best_errors;
    int errs;
    int lock_wait;

    cdr_adaptive #(
        .OVERSAMPLE(OS),
        .SYNC_STAGES(SYNC),
        .FRAC_BITS(8),
        .LOOP_GAIN(4),
        .LOCK_THRESH(LOCK_TH),
        .LOCK_RELEASE(8),
        .LOCK_STALE_BITS(32),
        .PHASE_DEADBAND(1),
        .LOOP_SETTLE_BITS(32)
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
        .lock_stale(lock_stale),
        .freq_trim(freq_trim)
    );

    always #5 clk = ~clk;

`ifdef ADAPT_DEBUG
    int dbg_edges;
    initial dbg_edges = 0;
    always @(posedge clk) begin
        if (dut.data_edge && dut.lock_q && dbg_edges < 30) begin
            $display("t=%0t edge phase=%0d err=%0d pd=%0d trim=%0d last=%0d settle=%0d",
                     $time, dut.phase_q, dut.phase_err, dut.pd_acc_q,
                     dut.freq_trim_q, dut.period_last_q, dut.settle_cnt_q);
            dbg_edges++;
        end
    end
`endif

    initial begin
        logic [14:0] state;
        state = 15'h7fff;
        for (int i = 0; i < N_BITS; i++) begin
            tx_bits[i] = state[14];
            state = {state[13:0], state[14] ^ state[13]};
        end
    end

    initial begin
        data_in = 1'b0;
        wait (rst_n);
        #3.7;
        for (int i = 0; i < N_BITS; i++) begin
            data_in = tx_bits[i];
            #(BIT_PERIOD);
        end
    end

    initial begin
        samples = 0;
        wait (rst_n);
        wait (lock);
        repeat (32) @(posedge clk);
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

        while (!lock && lock_wait < 5000) begin
            @(posedge clk);
            #1;
            lock_wait++;
        end
        if (!lock) begin
            $display("ERROR: adaptive CDR did not lock (PPM=%0.1f)", PPM);
            $fatal(1);
        end

        wait (samples == N_CHECK);
        #1;

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
                $display("ERROR: adaptive bit %0d got %0b expected %0b",
                         k, rx_bits[k], tx_bits[offset + k]);
                errors++;
            end
        end

        if (lock_stale) begin
            $display("ERROR: adaptive CDR asserted lock_stale");
            errors++;
        end

        if (errors != 0) begin
            $display("FAIL: tb_cdr_adaptive PPM=%0.1f errors=%0d offset=%0d best=%0d freq_trim=%0d",
                     PPM, errors, offset, best_errors, freq_trim);
            $fatal(1);
        end
        $display("PASS: tb_cdr_adaptive PPM=%0.1f bits=%0d offset=%0d freq_trim=%0d lock_wait=%0d",
                 PPM, N_CHECK, offset, freq_trim, lock_wait);
        $finish;
    end

endmodule
