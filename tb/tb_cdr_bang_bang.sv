`timescale 1ns/1ps

// 2x bang-bang CDR test with a deliberate TX/RX frequency offset.
//
// STATUS: this test currently FAILS (see rtl/cdr_bang_bang.sv).  It is kept as
// the acceptance test for the prototype and is intentionally NOT in make sim /
// scripts/regress.py.
//
// The data source runs at BIT_PERIOD = 20 ns * (1 + PPM/1e6) (2 samples/UI at a
// 10 ns RX clock), while the CDR clock is exactly 100 MHz.  After lock the test
// collects N_CHECK recovered bits, finds the startup offset, and requires every
// bit to match.  At 700 ppm this spans well over one UI, so a fixed-phase 2x
// sampler would slip.
module tb_cdr_bang_bang #(
    parameter real PPM      = 0.0,
    parameter int  N_CHECK  = 5000,
    parameter int  OS_PARAM = 4,
    parameter int  PH_OFF   = 0
);

    localparam real BIT_PERIOD = (OS_PARAM * 10.0) * (1.0 + PPM / 1000000.0);
    localparam int  N_BITS     = N_CHECK + 256;

    logic clk;
    logic rst_n;
    logic enable;
    logic data_in;
    logic data_out;
    logic sample_strobe;
    logic clk_out;
    logic lock;
    logic lock_stale;
    logic signed [12:0] freq_trim;

    logic [N_BITS-1:0]  tx_bits;
    logic [N_CHECK-1:0] rx_bits;

    int errors;
    int samples;
    int offset;
    int best_errors;
    int errs;
    int lock_wait;
    int sel_changes;
    logic [1:0] last_sel;

    cdr_bang_bang #(
        .OVERSAMPLE(OS_PARAM),
        .SYNC_STAGES(3),
        .ACQ_WINDOW(64),
        .LOCK_WINDOWS(2),
        .LOCK_STALE_BITS(256),
        .PHASE_CTRL_W(2),
        .FRAC_BITS(12)
    ) dut (
        .clk(clk),
        .rst_n(rst_n),
        .enable(enable),
        .flush(1'b0),
        .data_in(data_in),
        .phase_offset(PH_OFF[1:0]),
        .data_out(data_out),
        .sample_strobe(sample_strobe),
        .clk_out(clk_out),
        .lock(lock),
        .lock_stale(lock_stale),
        .freq_trim(freq_trim)
    );

    always #5 clk = ~clk;

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
        sel_changes = 0;
        last_sel = 2'd3;
        wait (rst_n);
        wait (lock);
        $display("INFO: bang_bang locked OS=%0d sel=%0d", OS_PARAM, dut.sel_q);
        last_sel = dut.sel_q;
        repeat (512) @(posedge clk);
        while (samples < N_CHECK) begin
            @(posedge clk);
            #1;
            if (dut.sel_q !== last_sel) begin
                sel_changes++;
                if (sel_changes <= 20)
                    $display("INFO: sel %0d -> %0d at bit %0d", last_sel, dut.sel_q, samples);
                last_sel = dut.sel_q;
            end
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
            $display("FAIL: tb_cdr_bang_bang did not lock (PPM=%0.1f)", PPM);
            $fatal(1);
        end

        wait (samples == N_CHECK);
        #1;

        best_errors = N_CHECK + 1;
        offset      = 0;
        // The first collected bit can be well past 64 UIs because lock needs
        // several acquisition windows; search the whole valid offset range.
        for (int off = 0; off + 64 <= N_BITS; off++) begin
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

        begin
            int first_bad;
            first_bad = -1;
            for (int k = 0; k < N_CHECK; k++) begin
                if (rx_bits[k] !== tx_bits[offset + k] && first_bad < 0)
                    first_bad = k;
            end
            $display("INFO: first_bad=%0d of %0d", first_bad, N_CHECK);
        end
        for (int k = 0; k < N_CHECK; k++) begin
            if (rx_bits[k] !== tx_bits[offset + k]) begin
                $display("ERROR: bang_bang bit %0d got %0b expected %0b",
                         k, rx_bits[k], tx_bits[offset + k]);
                errors++;
            end
        end

        if (lock_stale) begin
            $display("ERROR: bang_bang CDR asserted lock_stale");
            errors++;
        end

        if (errors != 0) begin
            for (int k = 0; k < 16; k++)
                $display("  cmp %0d rx=%0b tx=%0b", k, rx_bits[k], tx_bits[offset + k]);
            $display("FAIL: tb_cdr_bang_bang PPM=%0.1f errors=%0d offset=%0d best=%0d freq_trim=%0d sel_changes=%0d",
                     PPM, errors, offset, best_errors, freq_trim, sel_changes);
            $fatal(1);
        end
        $display("PASS: tb_cdr_bang_bang PPM=%0.1f bits=%0d offset=%0d freq_trim=%0d lock_wait=%0d",
                 PPM, N_CHECK, offset, freq_trim, lock_wait);
        $finish;
    end

endmodule
