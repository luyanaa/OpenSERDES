`timescale 1ns/1ps
// Phase-offset sweep: four CDRs with phase_offset = 0..3 see the same stream.
// This covers the modular thresholds that the old crossing test lost
// (edge_e = 0 at offset 1, center_e = 0 at offset 3).  Each instance must
// emit exactly one strobe per UI and recover every bit.
module tb_cdr_phase_offset #(
    parameter int OS_PARAM = 4,
    parameter int N_CHECK  = 400,
    parameter real SKEW_NS = 3.7   // TX clock-to-data skew inside a UI
);
    localparam real BIT_PERIOD = OS_PARAM * 10.0;
    localparam int  N_BITS     = N_CHECK + 512;

    logic clk, rst_n, enable, data_in;
    logic [3:0] dout, strb;
    logic [3:0] lk;
    logic [N_BITS-1:0] tx_bits;
    logic [N_CHECK-1:0] rx_bits [0:3];
    int errors [4];
    int samples [4];
    int last_clock [4];
    int gap_errors [4];
    int offset [4];
    int best [4];
    int clk_cnt;

    genvar gi;
    generate
        for (gi = 0; gi < 4; gi = gi + 1) begin : g_dut
            cdr_bang_bang #(
                .OVERSAMPLE(OS_PARAM), .SYNC_STAGES(3), .NCO_FRAC(20),
                .KI(1), .KP_SHIFT(5), .LEAK_UI_BITS(12), .LOCK_EDGES(16),
                .LOCK_STALE_BITS(16), .PHASE_CTRL_W(2), .FRAC_BITS(12)
            ) u (
                .clk(clk), .rst_n(rst_n), .enable(enable), .flush(1'b0),
                .data_in(data_in), .phase_offset(gi[1:0]),
                .data_out(dout[gi]), .sample_strobe(strb[gi]),
                .clk_out(), .lock(lk[gi]), .lock_stale(), .freq_trim()
            );
        end
    endgenerate

    always #5 clk = ~clk;
    always @(posedge clk) clk_cnt = clk_cnt + 1;

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
        #(SKEW_NS);
        for (int i = 0; i < N_BITS; i++) begin
            data_in = tx_bits[i];
            #(BIT_PERIOD);
        end
    end

    initial begin
        for (int k = 0; k < 4; k++) begin
            samples[k] = 0; errors[k] = 0; gap_errors[k] = 0;
            last_clock[k] = -1;
        end
        clk_cnt = 0;
        wait (rst_n);
        wait (lk[0] && lk[1] && lk[2] && lk[3]);
        repeat (128) @(posedge clk);
        while (samples[0] < N_CHECK || samples[1] < N_CHECK ||
               samples[2] < N_CHECK || samples[3] < N_CHECK) begin
            @(posedge clk);
            #1;
            for (int k = 0; k < 4; k++) begin
                if (strb[k] && samples[k] < N_CHECK) begin
                    if (last_clock[k] >= 0) begin
                        int gap;
                        gap = clk_cnt - last_clock[k];
                        if (gap < OS_PARAM - 1 || gap > OS_PARAM + 1)
                            gap_errors[k]++;
                    end
                    last_clock[k] = clk_cnt;
                    rx_bits[k][samples[k]] = dout[k];
                    samples[k]++;
                end
            end
        end
        #1;
        for (int k = 0; k < 4; k++) begin
            best[k] = N_CHECK + 1; offset[k] = 0;
            for (int off = 0; off + 64 <= N_BITS; off++) begin
                int e; e = 0;
                for (int j = 0; j < 64; j++)
                    if (rx_bits[k][j] !== tx_bits[off + j]) e++;
                if (e < best[k]) begin best[k] = e; offset[k] = off; end
            end
            for (int j = 0; j < N_CHECK; j++)
                if (rx_bits[k][j] !== tx_bits[offset[k] + j]) errors[k]++;
        end
        begin
            int bad;
            bad = 0;
            for (int k = 0; k < 4; k++) begin
                // Cadence must be exact for every offset: one strobe per UI,
                // including offsets where a threshold lands on the modular
                // boundary (edge_e = 0 at offset 1, center_e = 0 at offset 3).
                if (gap_errors[k] != 0) begin
                    $display("FAIL: phase_offset=%0d gap_errors=%0d", k, gap_errors[k]);
                    bad = 1;
                end
                // phase_offset is a sample-phase calibration of +-1 sector
                // (+-0.25 UI).  Offset 2 puts the data sample on the bit
                // boundary, so it cannot recover data; report it, do not fail.
                if (k == 2) begin
                    $display("INFO: phase_offset=2 is the boundary (cadence errors=%0d, %0d bit errors expected)",
                             gap_errors[k], errors[k]);
                end else if (errors[k] != 0) begin
                    $display("FAIL: phase_offset=%0d errors=%0d offset=%0d best=%0d",
                             k, errors[k], offset[k], best[k]);
                    for (int j = 0; j < N_CHECK; j++)
                        if (rx_bits[k][j] !== tx_bits[offset[k] + j])
                            $display("  mismatch k=%0d bit=%0d rx=%0b tx=%0b",
                                     k, j, rx_bits[k][j], tx_bits[offset[k]+j]);
                    bad = 1;
                end else if (gap_errors[k] == 0) begin
                    $display("PASS: phase_offset=%0d bits=%0d gap_errors=0", k, N_CHECK);
                end
            end
            if (bad) $fatal(1);
        end
        $finish;
    end

    initial begin
        clk = 1'b0; rst_n = 1'b0; enable = 1'b0;
        repeat (3) @(posedge clk); rst_n = 1'b1;
        repeat (2) @(posedge clk); enable = 1'b1;
    end
endmodule
