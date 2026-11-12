`timescale 1ns/1ps
// Generated-RX endpoint test.
//
// Unlike tb_cdr_bang_bang (which instantiates cdr_bang_bang with hand-written
// parameters), this test compiles the package the generator actually emits:
// it includes rtl/params.svh from the generated package and instantiates
// serdes_rx_digital with exactly those parameters.  That closes the gap where
// the standalone testbench and the delivered RTL could disagree on KI,
// KP_SHIFT, LOCK_STALE_BITS or the CDR_MODE mapping.
//
//   iverilog -I build/gen/bang_bang_4x/rtl tb/tb_rx_endpoint_cdr.sv \
//            build/gen/bang_bang_4x/rtl/*.sv
//
// PPM and PATTERN are runtime plusargs.
module tb_rx_endpoint_cdr #(
    parameter int N_CHECK = 5000
);
`include "params.svh"

    localparam int DW = `SERDES_DATA_WIDTH;
    localparam int WD = `SERDES_WORDS;
    localparam int OS = `SERDES_OVERSAMPLE;

    real ppm;
    int  pattern;
    real bit_period;

    logic clk, rst_n, enable, data_in;
    logic [WD-1:0][DW-1:0] par_data;
    logic par_valid, busy, lock, lock_stale, sample_strobe, clk_out;
    logic frame_locked, frame_valid, serial_data, serial_valid;
    logic signed [`SERDES_ADAPT_FRAC_BITS:0] freq_trim;
    logic [N_CHECK+512-1:0] tx_bits;
    logic [N_CHECK-1:0]     rx_bits;
    int errors, samples, offset, best_errors, errs, lock_wait;

    serdes_rx_digital #(
        .DATA_WIDTH(`SERDES_DATA_WIDTH),
        .WORDS(`SERDES_WORDS),
        .LSB_FIRST(`SERDES_LSB_FIRST),
        .OVERSAMPLE(`SERDES_OVERSAMPLE),
        .SYNC_STAGES(`SERDES_SYNC_STAGES),
        .LOCK_THRESH(`SERDES_LOCK_THRESH),
        .LOCK_STALE_BITS(`SERDES_LOCK_STALE_BITS),
        .CDR_MODE(`SERDES_CDR_MODE),
        .CDR_BYPASS(`SERDES_CDR_BYPASS),
        .ALIGN_MODE(`SERDES_ALIGN_MODE),
        .SYNC_LEN(`SERDES_SYNC_LEN),
        .SYNC_PATTERN(`SERDES_SYNC_PATTERN),
        .ADAPT_FRAC_BITS(`SERDES_ADAPT_FRAC_BITS),
        .ADAPT_LOOP_GAIN(`SERDES_ADAPT_LOOP_GAIN),
        .CLK_OUT_ENABLE(`SERDES_CLK_OUT_ENABLE)
    ) dut (
        .rx_clk(clk), .rx_rst_n(rst_n), .rx_enable(enable), .rx_flush(1'b0),
        .rx_align(1'b0), .rx_serial_in(data_in), .phase_offset('0),
        .rx_smp(data_in),
        /* verilator lint_off PINCONNECTEMPTY */
        .rx_dll_tap(),
        /* verilator lint_on PINCONNECTEMPTY */
        .rx_par_data(par_data), .rx_par_valid(par_valid), .rx_busy(busy),
        .rx_lock(lock), .rx_lock_stale(lock_stale),
        .rx_sample_strobe(sample_strobe), .rx_clk_out(clk_out),
        .rx_frame_locked(frame_locked), .rx_frame_valid(frame_valid),
        .rx_serial_data(serial_data), .rx_serial_valid(serial_valid),
        .rx_freq_trim(freq_trim)
    );

    always #5 clk = ~clk;

    task automatic get_cfg;
        ppm = 0.0; pattern = 0;
        void'($value$plusargs("PPM=%f", ppm));
        void'($value$plusargs("PATTERN=%d", pattern));
        bit_period = (OS * 10.0) * (1.0 + ppm / 1000000.0);
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
        // Prove the endpoint's internal loop parameters are the ones the
        // standalone testbench signs off on.
        $display("INFO: endpoint CDR_MODE=%0d OVERSAMPLE=%0d KI=%0d KP_SHIFT=%0d LEAK_UI_BITS=%0d LOCK_STALE_BITS=%0d",
                 `SERDES_CDR_MODE, `SERDES_OVERSAMPLE,
                 dut.g_bang_bang.u_cdr.KI, dut.g_bang_bang.u_cdr.KP_SHIFT,
                 dut.g_bang_bang.u_cdr.LEAK_UI_BITS,
                 dut.g_bang_bang.u_cdr.LOCK_STALE_BITS);
        repeat (64) @(posedge clk);
        while (samples < N_CHECK) begin
            @(posedge clk);
            #1;
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
            $display("FAIL: endpoint no lock PPM=%0.1f pattern=%0d", ppm, pattern);
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
            $display("ERROR: endpoint lock_stale asserted");
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
            $display("FAIL: tb_rx_endpoint_cdr PPM=%0.1f pattern=%0d errors=%0d offset=%0d best=%0d",
                     ppm, pattern, errors, offset, best_errors);
            $fatal(1);
        end
        $display("PASS: tb_rx_endpoint_cdr PPM=%0.1f pattern=%0d bits=%0d offset=%0d",
                 ppm, pattern, N_CHECK, offset);
        $finish;
    end
endmodule
