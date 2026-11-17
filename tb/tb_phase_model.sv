`timescale 1ps/1ps

// -----------------------------------------------------------------------------
// jitter_clock vs verification/channel/phase_model.py: bit-exact comparison.
//
// The vector file is written by the Python module as one 32-bit hex word per
// modelled edge (two's complement), so the RTL needs no text parser.  This TB
// runs `tb/jitter_clock.sv` with the same integer parameters and fails on the
// first mismatch, so the two implementations cannot drift apart unnoticed.
//
// Parameters arrive as a Verilog define set so one binary serves every case:
//
//   iverilog -g2012 -DTB_RJ=1311 -DTB_SJ=0 -DTB_SJ_F=0 -DTB_PPM=0 ...
//
// Cases exercised by scripts/regress.py: white RJ only, sinusoidal SJ only,
// ppm only, DCD only, and all of them together.
// -----------------------------------------------------------------------------
module tb_phase_model;

    localparam int MAXV = 4096;

    logic clk = 0, rst_n = 0, enable = 0;
    always #5 clk = ~clk;

    logic               edge_valid;
    logic [15:0]        edge_index;
    logic signed [31:0] tie_ps;
    logic [31:0]        edge_count;
    logic signed [31:0] tie_min_ps, tie_max_ps;

`ifndef TB_UI_PS
    localparam int UI_PS = 10000;
`else
    localparam int UI_PS = `TB_UI_PS;
`endif
`ifndef TB_SEED
    localparam int SEED = 16'hACE1;
`else
    localparam int SEED = `TB_SEED;
`endif
`ifndef TB_RJ
    localparam int RJ_RMS_UI_Q = 0;
`else
    localparam int RJ_RMS_UI_Q = `TB_RJ;
`endif
`ifndef TB_SJ
    localparam int SJ_PEAK_UI_Q = 0;
`else
    localparam int SJ_PEAK_UI_Q = `TB_SJ;
`endif
`ifndef TB_SJ_FREQ
    localparam int SJ_FREQ_HZ = 0;
`else
    localparam int SJ_FREQ_HZ = `TB_SJ_FREQ;
`endif
`ifndef TB_PPM
    localparam int PPM = 0;
`else
    localparam int PPM = `TB_PPM;
`endif
`ifndef TB_DCD
    localparam int DCD_PEAK_UI_Q = 0;
`else
    localparam int DCD_PEAK_UI_Q = `TB_DCD;
`endif
`ifndef TB_DCD_PATTERN
`define TB_DCD_PATTERN 0
`endif
`ifndef TB_N_EDGES
    localparam int N_EDGES = 64;
`else
    localparam int N_EDGES = `TB_N_EDGES;
`endif

    // Patterned DCD stimulus: off -> constant 0; on -> alternating, so there
    // is a transition into every edge and the DCD sign alternates.
    logic dcd_pattern_bit;
    logic       dcd_toggle_q;
    assign dcd_pattern_bit = (`TB_DCD_PATTERN == 0) ? 1'b0 : dcd_toggle_q;

    jitter_clock #(
        .UI_PS(UI_PS), .SEED(SEED),
        .RJ_RMS_UI_Q(RJ_RMS_UI_Q), .SJ_PEAK_UI_Q(SJ_PEAK_UI_Q),
        .SJ_FREQ_HZ(SJ_FREQ_HZ), .PPM(PPM),
        .DCD_PEAK_UI_Q(DCD_PEAK_UI_Q),
        .N_EDGES(N_EDGES)
    ) dut (
        .clk(clk), .rst_n(rst_n), .enable(enable),
        .dcd_bit(dcd_pattern_bit),
        .edge_valid(edge_valid), .edge_index(edge_index), .tie_ps(tie_ps),
        .edge_count(edge_count), .tie_min_ps(tie_min_ps),
        .tie_max_ps(tie_max_ps)
    );

    int errors;
    int idx;
    logic [31:0] expected [MAXV];
    string vec_path;
    logic dcd_bit_q;

    initial begin
        errors = 0;
        idx    = 0;
        dcd_toggle_q = 1'b0;
        if (!$value$plusargs("vector=%s", vec_path)) begin
            $display("ERROR: +vector=<file> is required");
            $finish;
        end
        $readmemh(vec_path, expected);
        $display("INFO: reference %s (%0d edges)", vec_path, N_EDGES);

        repeat (3) @(posedge clk);
        rst_n  = 1'b1;
        enable = 1'b1;

        // One edge every 13 clk cycles; drain with margin.
        for (int t = 0; t < 13 * N_EDGES + 64; t++) begin
            @(posedge clk);
            #1;
            if (edge_valid) begin
                if (idx >= N_EDGES) begin
                    $display("ERROR: extra edge %0d (only %0d expected)",
                             idx, N_EDGES);
                    errors = errors + 1;
                end else if (tie_ps !== $signed(expected[idx])) begin
                    $display("ERROR: edge %0d tie %0d != reference %0d",
                             idx, $signed(tie_ps), $signed(expected[idx]));
                    errors = errors + 1;
                end
                if (edge_index != 16'(idx)) begin
                    $display("ERROR: edge index %0d != %0d", edge_index, idx);
                    errors = errors + 1;
                end
                idx = idx + 1;
            end
            if (dut.valid_q)
                dcd_toggle_q <= ~dcd_toggle_q;
        end

        if (idx != N_EDGES) begin
            $display("ERROR: produced %0d edges, reference has %0d",
                     idx, N_EDGES);
            errors = errors + 1;
        end
        if (tie_max_ps < tie_min_ps) begin
            $display("ERROR: min/max telemetry never updated (%0d/%0d)",
                     tie_min_ps, tie_max_ps);
            errors = errors + 1;
        end

        if (errors == 0)
            $display("PASS: tb_phase_model edges=%0d bit-exact (min=%0d max=%0d)",
                     idx, tie_min_ps, tie_max_ps);
        else
            $display("FAIL: tb_phase_model errors=%0d edges=%0d/%0d",
                     errors, idx, N_EDGES);
        $finish;
    end

    initial begin
        #20_000_000;
        $display("FAIL: tb_phase_model timeout edges=%0d", idx);
        $finish;
    end

endmodule
