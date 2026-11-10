`timescale 1ns/1ps
// 4x bang-bang CDR test with a deliberate TX/RX frequency offset.
// The data source runs at BIT_PERIOD = OS*10ns*(1+PPM/1e6) while the CDR clock
// is exactly 100 MHz.  After lock the test collects N_CHECK recovered bits,
// finds the start offset, and requires every bit to match.
//
// PPM and PATTERN are runtime plusargs (vvp ... +PPM=700 +PATTERN=1) because
// this Icarus build has no runtime -P; a compile-time -P override silently
// left every sweep entry at its default in the past.
module tb_cdr_bang_bang #(
    parameter int  N_CHECK  = 5000,
    parameter int  OS_PARAM = 4,
    parameter real PPM_DEF  = 0.0,
    parameter int  PAT_DEF  = 0,  // 0=PRBS, 1=PRBS+long run+PRBS, 2=bursty
    parameter int  KI_DEF      = 1,
    parameter int  KP_SHIFT_DEF = 5,
    parameter int  LEAK_DEF     = 12   // UIs per LSB, <0 disables
);
    real ppm;
    int  pattern;
    real bit_period;

    logic clk, rst_n, enable, data_in;
    logic data_out, sample_strobe, clk_out, lock, lock_stale;
    logic signed [12:0] freq_trim;
    logic [N_CHECK+512-1:0] tx_bits;
    logic [N_CHECK-1:0]     rx_bits;
    int errors, samples, offset, best_errors, errs, lock_wait;
    int ph_hist [0:N_CHECK-1];
    int fq_hist [0:N_CHECK-1];
    int s_hist  [0:N_CHECK-1];

    // Same parameter values the generator leaves on serdes_rx_digital's
    // g_bang_bang instance (module defaults: KI=1, KP_SHIFT=5,
    // LOCK_STALE_BITS=16, LEAK_UI_BITS=12).  tb_rx_endpoint_cdr additionally
    // proves this by instantiating the generated endpoint itself.
    cdr_bang_bang #(
        .OVERSAMPLE(OS_PARAM), .SYNC_STAGES(3), .NCO_FRAC(20),
        .KI(KI_DEF), .KP_SHIFT(KP_SHIFT_DEF), .LEAK_UI_BITS(LEAK_DEF), .LOCK_EDGES(16),
        .LOCK_STALE_BITS(16), .PHASE_CTRL_W(2), .FRAC_BITS(12)
    ) dut (
        .clk(clk), .rst_n(rst_n), .enable(enable), .flush(1'b0),
        .data_in(data_in), .phase_offset(2'd0), .data_out(data_out),
        .sample_strobe(sample_strobe), .clk_out(clk_out), .lock(lock),
        .lock_stale(lock_stale), .freq_trim(freq_trim)
    );

    always #5 clk = ~clk;

    task automatic get_cfg;
        ppm     = PPM_DEF;
        pattern = PAT_DEF;
        void'($value$plusargs("PPM=%f", ppm));
        void'($value$plusargs("PATTERN=%d", pattern));
        bit_period = (OS_PARAM * 10.0) * (1.0 + ppm / 1000000.0);
    endtask

    initial begin
        logic [14:0] state;
        state = 15'h7fff;
        get_cfg;
        for (int i = 0; i < N_CHECK+512; i++) begin
            tx_bits[i] = state[14];
            state = {state[13:0], state[14] ^ state[13]};
            if (pattern == 1 && i >= 512 && i < 768)
                tx_bits[i] = 1'b0;              // 256-bit constant run
            if (pattern == 2 && (i / 32) % 2 == 1)
                tx_bits[i] = (i % 32) < 16 ? 1'b1 : 1'b0;
            // pattern 3: low transition density.  Two PRBS bits every 16
            // line bits, zero elsewhere: ~6% density with long vote droughts.
            // Still one bit per UI, so the bit-exact comparison stays valid;
            // the offset search uses a 256-bit window so the sparse content
            // is still unambiguous.
            if (pattern == 3)
                tx_bits[i] = tx_bits[i] & ((i % 16) < 2);  // bursts
        end
    end

    initial begin
        get_cfg;
        data_in = 1'b0;
        wait (rst_n);
        #3.7;
        for (int i = 0; i < N_CHECK+512; i++) begin
            data_in = tx_bits[i];
            #(bit_period);
        end
    end

    initial begin
        samples = 0;
        wait (rst_n);
        wait (lock);
        $display("INFO: bang_bang locked PPM=%0.1f pattern=%0d OS=%0d KI=%0d KP_SHIFT=%0d LEAK_UI_BITS=%0d freq_trim=%0d",
                 ppm, pattern, OS_PARAM, KI_DEF, KP_SHIFT_DEF, LEAK_DEF, freq_trim);
        repeat (64) @(posedge clk);
        while (samples < N_CHECK) begin
            @(posedge clk);
            #1;
            if (sample_strobe) begin
                rx_bits[samples] = data_out;
                ph_hist[samples] = int'(dut.phase_q);
                fq_hist[samples] = int'(dut.freq_q);
                s_hist[samples]  = int'(dut.s);
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
        while (!lock && lock_wait < 20000) begin
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
        for (int k = 0; k < N_CHECK; k++)
            if (rx_bits[k] !== tx_bits[offset + k]) errors++;
        if (lock_stale) begin
            $display("ERROR: lock_stale asserted");
            errors++;
        end
        if (errors != 0) begin
            begin : show_first
                int shown; shown = 0;
                for (int k = 0; k < N_CHECK && shown < 4; k++)
                    if (rx_bits[k] !== tx_bits[offset+k]) begin
                        $display("  mismatch at bit %0d rx=%0b tx=%0b", k, rx_bits[k], tx_bits[offset+k]);
                        for (int j = (k < 6) ? 0 : k-6; j < k+6 && j < N_CHECK; j++)
                            $display("    bit %0d ph=%0d fq=%0d s=%0d rx=%0b tx=%0b%s",
                                     j, ph_hist[j], fq_hist[j], s_hist[j], rx_bits[j],
                                     tx_bits[offset+j], (j==k) ? "  <--" : "");
                        shown++;
                    end
            end
            $display("FAIL: tb_cdr_bang_bang PPM=%0.1f pattern=%0d errors=%0d offset=%0d best=%0d freq_trim=%0d",
                     ppm, pattern, errors, offset, best_errors, freq_trim);
            $fatal(1);
        end
        $display("PASS: tb_cdr_bang_bang PPM=%0.1f pattern=%0d bits=%0d offset=%0d freq_trim=%0d",
                 ppm, pattern, N_CHECK, offset, freq_trim);
        $finish;
    end
endmodule
