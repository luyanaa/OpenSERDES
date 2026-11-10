`timescale 1ns/1ps
// 4x bang-bang CDR test with a deliberate TX/RX frequency offset.
// The data source runs at BIT_PERIOD = OS*10ns*(1+PPM/1e6) while the CDR clock
// is exactly 100 MHz.  After lock the test collects N_CHECK recovered bits,
// finds the start offset, and requires every bit to match.
module tb_cdr_bang_bang #(
    parameter real PPM      = 0.0,
    parameter int  N_CHECK  = 5000,
    parameter int  OS_PARAM = 4
);
    localparam real BIT_PERIOD = (OS_PARAM * 10.0) * (1.0 + PPM / 1000000.0);
    localparam int  N_BITS     = N_CHECK + 512;

    logic clk, rst_n, enable, data_in;
    logic data_out, sample_strobe, clk_out, lock, lock_stale;
    logic signed [12:0] freq_trim;
    logic [N_BITS-1:0]  tx_bits;
    logic [N_CHECK-1:0] rx_bits;
    int errors, samples, offset, best_errors, errs, lock_wait;

    cdr_bang_bang #(
        .OVERSAMPLE(OS_PARAM), .SYNC_STAGES(3), .NCO_FRAC(20),
        .KI(2), .KP_SHIFT(3), .LOCK_EDGES(16), .LOCK_STALE_BITS(256),
        .PHASE_CTRL_W(2), .FRAC_BITS(12)
    ) dut (
        .clk(clk), .rst_n(rst_n), .enable(enable), .flush(1'b0),
        .data_in(data_in), .phase_offset(2'd0), .data_out(data_out),
        .sample_strobe(sample_strobe), .clk_out(clk_out), .lock(lock),
        .lock_stale(lock_stale), .freq_trim(freq_trim)
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
        wait (rst_n);
        wait (lock);
        $display("INFO: bang_bang locked OS=%0d freq_trim=%0d", OS_PARAM, freq_trim);
        repeat (64) @(posedge clk);
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
        clk = 1'b0; rst_n = 1'b0; enable = 1'b0;
        errors = 0; lock_wait = 0;
        repeat (3) @(posedge clk); rst_n = 1'b1;
        repeat (2) @(posedge clk); enable = 1'b1;
        while (!lock && lock_wait < 20000) begin
            @(posedge clk); #1; lock_wait++;
        end
        if (!lock) begin
            $display("FAIL: no lock PPM=%0.1f", PPM); $fatal(1);
        end
        wait (samples == N_CHECK);
        #1;
        best_errors = N_CHECK + 1; offset = 0;
        for (int off = 0; off + 64 <= N_BITS; off++) begin
            errs = 0;
            for (int k = 0; k < 64; k++)
                if (rx_bits[k] !== tx_bits[off + k]) errs++;
            if (errs < best_errors) begin best_errors = errs; offset = off; end
        end
        for (int k = 0; k < N_CHECK; k++)
            if (rx_bits[k] !== tx_bits[offset + k]) errors++;
        if (lock_stale) begin
            $display("ERROR: lock_stale asserted"); errors++;
        end
        if (errors != 0) begin
            for (int k = 0; k < 64; k++)
                $display("  cmp %0d rx=%0b tx=%0b", k, rx_bits[k], tx_bits[offset+k]);
            $display("FAIL: tb_cdr_bang_bang PPM=%0.1f errors=%0d offset=%0d best=%0d freq_trim=%0d",
                     PPM, errors, offset, best_errors, freq_trim);
            $fatal(1);
        end
        $display("PASS: tb_cdr_bang_bang PPM=%0.1f bits=%0d offset=%0d freq_trim=%0d",
                 PPM, N_CHECK, offset, freq_trim);
        $finish;
    end
endmodule
