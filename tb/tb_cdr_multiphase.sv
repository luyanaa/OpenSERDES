`timescale 1ns/1ps
// 4x bang-bang CDR with the DLL phase-select backend.
//
// The DUT is the digital half (rtl/cdr_multiphase.sv).  The DLL/phase mux is
// modelled here: on every 100 MHz edge the sampler takes data_in at
// (tap + 0.5) * (10 ns / 8) = 0.625 .. 9.375 ns, i.e. the eight taps land in
// the middle of each 1.25 ns slot rather than on a clock edge.  The DUT reads
// the sample on the following edge, which is the same one-cycle hand-off the
// real sampler has.
//
// PPM / PPM2 / STEP_AT / PATTERN are runtime plusargs (no runtime -P in this
// Icarus build).
module tb_cdr_multiphase #(
    parameter int  N_CHECK  = 5000,
    parameter real PPM_DEF  = 0.0,
    parameter int  PAT_DEF  = 0,
    parameter int  KI_DEF      = 1,
    parameter int  KP_SHIFT_DEF = 2,
    parameter int  LEAK_DEF     = 12,
    parameter real PPM2_DEF     = 0.0,
    parameter int  STEP_AT_DEF  = 0,
    // Must match the delivered endpoint default
    // (serdes_rx_digital.DLL_FREQ_FRAC = 8).  Run with -P...=11 only for the
    // explicit 11-bit comparison.
    parameter int  FREQ_FRAC_DEF = 8
);
    real ppm;
    int  pattern;
    int  ph_off;
    logic [1:0] ph_sel;
    real bit_period;

    logic clk, rst_n, enable, data_in;
    logic data_out, sample_strobe, clk_out, lock, lock_stale;
    logic [2:0] tap;
    logic signed [12:0] freq_trim;
    logic smp_sel;
    logic [N_CHECK+512-1:0] tx_bits;
    logic [N_CHECK-1:0]     rx_bits;
    int errors, samples, offset, best_errors, errs, lock_wait;
    real ppm2;
    int step_at;
    real period1, period2;
    int run_bits, best_run, first_err;
    int ph_hist [0:N_CHECK-1];
    int fq_hist [0:N_CHECK-1];
    int tap_hist [0:N_CHECK-1];
    int s_hist  [0:N_CHECK-1];

    cdr_multiphase #(
        .OVERSAMPLE(4), .PH_W(3), .FREQ_FRAC(FREQ_FRAC_DEF),
        .KI(KI_DEF), .KP_SHIFT(KP_SHIFT_DEF), .LEAK_UI_BITS(LEAK_DEF),
        .LOCK_EDGES(16), .LOCK_STALE_BITS(16),
        .PHASE_CTRL_W(2), .FRAC_BITS(12)
    ) dut (
        .clk(clk), .rst_n(rst_n), .enable(enable), .flush(1'b0),
        .smp(smp_sel), .phase_offset(ph_sel),
        .tap(tap), .data_out(data_out), .sample_strobe(sample_strobe),
        .clk_out(clk_out), .lock(lock), .lock_stale(lock_stale),
        .freq_trim(freq_trim)
    );

    always #5 clk = ~clk;

    // Eight-tap sampler with the selected phase.  The model reads data_in at
    // (tap + 0.5) * 1.25 ns, not at the clk edge, and the DUT sees the result
    // on the next clock (see tb/dll_sampler_model.sv and tb_dll_sampler.sv).
    dll_sampler_model #(.SLOT_NS(1.25)) u_sampler (
        .clk(clk),
        .rst_n(rst_n),
        .data_in(data_in),
        .tap(tap),
        .smp(smp_sel)
    );

    task automatic get_cfg;
        ppm     = PPM_DEF;
        pattern = PAT_DEF;
        void'($value$plusargs("PPM=%f", ppm));
        void'($value$plusargs("PATTERN=%d", pattern));
        ph_off = 0;
        void'($value$plusargs("PH_OFF=%d", ph_off));
        ph_sel = ph_off[1:0];
        ppm2    = PPM2_DEF;
        step_at = STEP_AT_DEF;
        void'($value$plusargs("PPM2=%f", ppm2));
        void'($value$plusargs("STEP_AT=%d", step_at));
        period1 = 40.0 * (1.0 + ppm  / 1000000.0);
        period2 = 40.0 * (1.0 + ppm2 / 1000000.0);
        bit_period = period1;
    endtask

    initial begin
        logic [14:0] state;
        state = 15'h7fff;
        get_cfg;
        for (int i = 0; i < N_CHECK+512; i++) begin
            tx_bits[i] = state[14];
            state = {state[13:0], state[14] ^ state[13]};
            if (pattern == 1 && i >= 512 && i < 768)
                tx_bits[i] = 1'b0;
            if (pattern == 2 && (i / 32) % 2 == 1)
                tx_bits[i] = (i % 32) < 16 ? 1'b1 : 1'b0;
            if (pattern == 3)
                tx_bits[i] = tx_bits[i] & ((i % 16) < 2);
        end
    end

    initial begin
        get_cfg;
        data_in = 1'b0;
        wait (rst_n);
        #3.7;
        for (int i = 0; i < N_CHECK+512; i++) begin
            data_in = tx_bits[i];
            #((step_at > 0 && i >= step_at) ? period2 : period1);
        end
    end

    initial begin
        samples = 0;
        wait (rst_n);
        wait (lock);
        $display("INFO: multiphase locked PPM=%0.1f pattern=%0d FREQ_FRAC=%0d KI=%0d KP_SHIFT=%0d LEAK_UI_BITS=%0d freq_trim=%0d",
                 ppm, pattern, FREQ_FRAC_DEF, KI_DEF, KP_SHIFT_DEF, LEAK_DEF, freq_trim);
        repeat (64) @(posedge clk);
        while (samples < N_CHECK) begin
            @(posedge clk);
            #1;
            if (sample_strobe) begin
                rx_bits[samples] = data_out;
                tap_hist[samples] = int'(tap);
                fq_hist[samples] = int'(dut.freq_q);
                ph_hist[samples] = int'({dut.slot_q, dut.tap_q});
                s_hist[samples]  = int'(smp_sel);
                samples++;
            end
        end
    end

    initial begin
        clk = 1'b0; rst_n = 1'b0; enable = 1'b0;
        get_cfg;
        errors = 0; lock_wait = 0;
        repeat (3) @(posedge clk); rst_n = 1'b1;
        repeat (2) @(posedge clk); enable = 1'b1;
        while (!lock && lock_wait < 40000) begin
            @(posedge clk); #1; lock_wait++;
        end
        if (!lock) begin
            $display("FAIL: no lock PPM=%0.1f pattern=%0d", ppm, pattern); $fatal(1);
        end
        wait (samples == N_CHECK);
        #1;
        best_errors = N_CHECK + 1; offset = 0;
        for (int off = 0; off + 256 <= N_CHECK+512; off++) begin
            errs = 0;
            for (int k = 0; k < 256; k++)
                if (rx_bits[k] !== tx_bits[off + k]) errs++;
            if (errs < best_errors) begin best_errors = errs; offset = off; end
        end
        run_bits = 0; best_run = 0; first_err = -1;
        for (int k = 0; k < N_CHECK; k++) begin
            if (rx_bits[k] !== tx_bits[offset + k]) begin
                if (first_err < 0) first_err = k;
                run_bits = 0;
                errors++;
            end else begin
                run_bits++;
                if (run_bits > best_run) best_run = run_bits;
            end
        end
        $display("INFO: acquisition lock_wait=%0d clocks, longest error-free run=%0d bits, first error at %0d",
                 lock_wait, best_run, first_err);
        if (lock_stale) begin
            $display("ERROR: lock_stale asserted");
            errors++;
        end

        begin
            int fmin, fmax, k;
            longint fsum;
            fmin = 32'sh7fffffff; fmax = -32'sh7fffffff; fsum = 0;
            for (k = N_CHECK/2; k < N_CHECK; k++) begin
                if (fq_hist[k] < fmin) fmin = fq_hist[k];
                if (fq_hist[k] > fmax) fmax = fq_hist[k];
                fsum += fq_hist[k];
            end
            $display("INFO: steady freq_q min=%0d max=%0d span=%0d mean=%0d over %0d bits",
                     fmin, fmax, fmax - fmin,
                     int'(fsum / (N_CHECK - N_CHECK/2)), N_CHECK - N_CHECK/2);
        end

        if (step_at > 0) begin
            int last_err, k;
            last_err = -1;
            for (k = 0; k < N_CHECK; k++)
                if (rx_bits[k] !== tx_bits[offset + k]) last_err = k;
            $display("INFO: step at bit %0d (PPM=%0.1f -> %0.1f), last error at %0d, relock=%0d bits",
                     step_at, ppm, ppm2, last_err, (last_err < 0) ? 0 : last_err - step_at);
        end

        if (errors != 0) begin
            begin : show_first
                int shown; shown = 0;
                for (int k = 0; k < N_CHECK && shown < 8; k++)
                    if (rx_bits[k] !== tx_bits[offset + k]) begin
                        $display("ERROR: bit %0d rx=%b tx=%b tap=%0d freq=%0d",
                                 k, rx_bits[k], tx_bits[offset+k], tap_hist[k], fq_hist[k]);
                        shown++;
                    end
            end
            $display("FAIL: %0d bit errors at PPM=%0.1f pattern=%0d", errors, ppm, pattern);
            $fatal(1);
        end
        $display("PASS: %0d bits, 0 errors at PPM=%0.1f pattern=%0d", N_CHECK, ppm, pattern);
        $finish;
    end
endmodule
