`timescale 1ns/1ps
// DLL phase-select endpoint test (CDR_MODE=4).
//
// This closes the integration gap left by tb_cdr_multiphase: that test drives
// cdr_multiphase alone with a hand-written sampler.  Here the full
// serdes_rx_digital top is instantiated with CDR_MODE=4, and the DLL/phase
// mux/sampler are modelled at the same (tap + 0.5) * 1.25 ns phase as the
// core test.  The DUT selects the phase on rx_dll_tap and reads the sampled
// bit back on rx_smp on the following clk, which is the interface a real
// analog macro provides (docs/dll_backend.md section 2).
//
// Parameters are hand-written and match the delivered cdr_multiphase defaults
// (PH_W=3, FREQ_FRAC=8, KI=1, KP_SHIFT=2, LEAK_UI_BITS=12); the hierarchical
// probe below fails elaboration if the mode-4 branch is missing.
//
// PPM / PATTERN are runtime plusargs (this Icarus build has no runtime -P).
module tb_rx_endpoint_dll #(
    parameter int N_CHECK = 5000
);

    localparam int DW = 32;
    localparam int WD = 8;

    real ppm;
    int  pattern;
    real bit_period;

    logic clk, rst_n, enable, data_in;
    logic rx_smp;
    logic [2:0] dll_tap;
    logic [WD-1:0][DW-1:0] par_data;
    logic par_valid, busy, lock, lock_stale, sample_strobe, clk_out;
    logic frame_locked, frame_valid, serial_data, serial_valid;
    logic signed [12:0] freq_trim;
    logic [N_CHECK+512-1:0] tx_bits;
    logic [N_CHECK-1:0]     rx_bits;
    int errors, samples, offset, best_errors, errs, lock_wait;
    int tap_min, tap_max;

    serdes_rx_digital #(
        .DATA_WIDTH(DW),
        .WORDS(WD),
        .LSB_FIRST(1'b1),
        .OVERSAMPLE(4),
        .SYNC_STAGES(3),
        .LOCK_THRESH(8),
        .LOCK_STALE_BITS(16),
        .CDR_MODE(4),
        .CDR_BYPASS(1'b0),
        .ALIGN_MODE(0),
        .SYNC_LEN(8),
        .SYNC_PATTERN(8'hBC),
        .ADAPT_FRAC_BITS(12),
        .ADAPT_LOOP_GAIN(8),
        .CLK_OUT_ENABLE(1'b1),
        .DLL_PH_W(3)
    ) dut (
        .rx_clk(clk), .rx_rst_n(rst_n), .rx_enable(enable), .rx_flush(1'b0),
        .rx_align(1'b0), .rx_serial_in(data_in), .phase_offset('0),
        .rx_smp(rx_smp), .rx_dll_tap(dll_tap),
        .rx_par_data(par_data), .rx_par_valid(par_valid), .rx_busy(busy),
        .rx_lock(lock), .rx_lock_stale(lock_stale),
        .rx_sample_strobe(sample_strobe), .rx_clk_out(clk_out),
        .rx_frame_locked(frame_locked), .rx_frame_valid(frame_valid),
        .rx_serial_data(serial_data), .rx_serial_valid(serial_valid),
        .rx_freq_trim(freq_trim)
    );

    always #5 clk = ~clk;

    // Analogue phase mux + sampler: take data_in at (tap + 0.5) * 1.25 ns
    // after the clk edge and present it on rx_smp for the following edge.
    always @(posedge clk) begin
        if (rst_n) begin
            #0.01;  // let the DUT's non-blocking tap update settle
            rx_smp <= #((dll_tap + 0.5) * 1.25) data_in;
        end
    end

    task automatic get_cfg;
        ppm = 0.0; pattern = 0;
        void'($value$plusargs("PPM=%f", ppm));
        void'($value$plusargs("PATTERN=%d", pattern));
        bit_period = (4 * 10.0) * (1.0 + ppm / 1000000.0);
    endtask

    initial begin
        logic [14:0] st;
        st = 15'h7fff;
        get_cfg;
        for (int i = 0; i < N_CHECK+512; i++) begin
            tx_bits[i] = st[14];
            st = {st[13:0], st[14] ^ st[13]};
            if (pattern == 1 && i >= 512 && i < 768)
                tx_bits[i] = 1'b0;
            if (pattern == 2 && (i / 32) % 2 == 1)
                tx_bits[i] = (i % 32) < 16 ? 1'b1 : 1'b0;
            // pattern 3: low transition density, two PRBS bits every 16 line
            // bits (~6%) with long vote droughts.
            if (pattern == 3)
                tx_bits[i] = tx_bits[i] & ((i % 16) < 2);
        end
    end

    initial begin
        get_cfg;
        data_in = 1'b0;
        rx_smp  = 1'b0;
        wait (rst_n);
        #3.7;
        for (int i = 0; i < N_CHECK+512; i++) begin
            data_in = tx_bits[i];
            #(bit_period);
        end
    end

    initial begin
        samples = 0;
        tap_min = 7;
        tap_max = 0;
        wait (rst_n);
        wait (lock);
        // Prove the endpoint elaborated the mode-4 branch with the delivered
        // core parameters, not the fixed-CDR fall-through.
        $display("INFO: endpoint DLL CDR_MODE=4 PH_W=%0d FREQ_FRAC=%0d KI=%0d KP_SHIFT=%0d LEAK_UI_BITS=%0d",
                 dut.g_dll.u_cdr.PH_W, dut.g_dll.u_cdr.FREQ_FRAC,
                 dut.g_dll.u_cdr.KI, dut.g_dll.u_cdr.KP_SHIFT,
                 dut.g_dll.u_cdr.LEAK_UI_BITS);
        repeat (64) @(posedge clk);
        while (samples < N_CHECK) begin
            @(posedge clk);
            #1;
            if (dll_tap < tap_min) tap_min = dll_tap;
            if (dll_tap > tap_max) tap_max = dll_tap;
            if (serial_valid) begin
                rx_bits[samples] = serial_data;
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
            $display("FAIL: endpoint DLL no lock PPM=%0.1f pattern=%0d", ppm, pattern);
            $fatal(1);
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
            $display("ERROR: endpoint DLL lock_stale asserted");
            errors++;
        end
        if (errors != 0) begin
            begin : show_first
                int shown; shown = 0;
                for (int k = 0; k < N_CHECK && shown < 8; k++)
                    if (rx_bits[k] !== tx_bits[offset+k]) begin
                        $display("  mismatch at bit %0d rx=%0b tx=%0b", k, rx_bits[k], tx_bits[offset+k]);
                        shown++;
                    end
            end
            $display("FAIL: tb_rx_endpoint_dll PPM=%0.1f pattern=%0d errors=%0d offset=%0d best=%0d tap=%0d..%0d",
                     ppm, pattern, errors, offset, best_errors, tap_min, tap_max);
            $fatal(1);
        end
        $display("PASS: tb_rx_endpoint_dll PPM=%0.1f pattern=%0d bits=%0d offset=%0d tap=%0d..%0d freq_trim=%0d",
                 ppm, pattern, N_CHECK, offset, tap_min, tap_max, freq_trim);
        $finish;
    end
endmodule
