`timescale 1ns/1ps
// -----------------------------------------------------------------------------
// TB for the quarter-rate TX parallel MUX + standalone PRBS31 TX macro.
//
// Generates the ideal clock set (500 MHz 4-phase, 500 MHz word clock leading
// ph0 by 250 ps, 1 GHz DDR reference sampler) and checks that the serial
// output reconstructs the PRBS31 sequence bit-exactly, in order, at the right
// time:
//   - samples serial_out on BOTH edges of the 1 GHz reference (2 GSample/s,
//     one sample per 500 ps UI -> DDR-capable checker),
//   - counts bit errors total and per phase (lane) slot,
//   - measures every serial_out transition against the ideal 500 ps UI grid
//     and reports max |deviation| per phase (inter-phase skew).
//
// Also runs a smoke check of tx_gearbox_stage (8 -> 4 registered halving).
//
// Run (vvp memory cap per repo convention):
//   iverilog -g2012 -o build/tb_tx_parallel_mux.vvp \
//     rtl/tx_prbs31_macro.sv rtl/tx_parallel_mux.sv rtl/tx_gearbox.sv \
//     pma/tx_driver.sv tb/tb_tx_parallel_mux.sv
//   (ulimit -v 4000000; vvp build/tb_tx_parallel_mux.vvp)
// -----------------------------------------------------------------------------
module tb_tx_parallel_mux;

    localparam int unsigned NBITS = 20000;   // PRBS31 bits to verify
    localparam real         RST_AT = 6.3;    // reset deassert (ns)
    localparam real         BASE   = 8.5;    // first checked UI boundary (ns)

    // ---- DUT signals --------------------------------------------------------
    logic        clk_word;
    logic [3:0]  clk_ph;
    logic        rst_n;
    logic        enable;
    logic        clear;
    logic        serial;
    logic        dv;
    logic [3:0]  nib_dbg;

    logic        clk_ref;                    // 1 GHz DDR sampling reference

    tx_prbs31_macro #(.PRBS_SEED(31'h7FFF_FFFF)) dut (
        .clk_word  (clk_word),
        .clk_ph    (clk_ph),
        .rst_n     (rst_n),
        .enable    (enable),
        .clear     (clear),
        .serial_out(serial),
        .data_valid(dv),
        .nibble_dbg(nib_dbg)
    );

    // ---- Ideal clock generation ---------------------------------------------
    // clk_word: 500 MHz, posedge 250 ps before every clk_ph[0] posedge.
    initial begin
        clk_word = 1'b0;
        #0.25;
        forever begin
            clk_word = 1'b1; #1.0;
            clk_word = 1'b0; #1.0;
        end
    end

    // clk_ph[i]: 500 MHz, 50% duty, posedges 500 ps apart in phase order.
    initial begin
        clk_ph[0] = 1'b0;
        #0.5;
        forever begin
            clk_ph[0] = 1'b1; #1.0;
            clk_ph[0] = 1'b0; #1.0;
        end
    end
    initial begin
        clk_ph[1] = 1'b0;
        #1.0;
        forever begin
            clk_ph[1] = 1'b1; #1.0;
            clk_ph[1] = 1'b0; #1.0;
        end
    end
    initial begin
        clk_ph[2] = 1'b0;
        #1.5;
        forever begin
            clk_ph[2] = 1'b1; #1.0;
            clk_ph[2] = 1'b0; #1.0;
        end
    end
    initial begin
        clk_ph[3] = 1'b0;
        #2.0;
        forever begin
            clk_ph[3] = 1'b1; #1.0;
            clk_ph[3] = 1'b0; #1.0;
        end
    end

    // 1 GHz reference: posedge at 8.75 + 1.0k (UI 0,2,4.. mid), negedge at
    // 9.25 + 1.0k (UI 1,3,.. mid) -> one sample per UI across both edges.
    initial begin
        clk_ref = 1'b0;
        #8.75;
        forever begin
            clk_ref = 1'b1; #0.5;
            clk_ref = 1'b0; #0.5;
        end
    end

    // ---- PRBS31 reference (same convention as rtl/prbs_gen.sv: MSB first) ----
    logic [30:0] ref_state;
    function automatic bit ref_next();
        bit b;
        b         = ref_state[30];
        ref_state = {ref_state[29:0], ref_state[30] ^ ref_state[27]};
        return b;
    endfunction

    int unsigned bit_idx;
    int unsigned errors;
    int unsigned phase_errors [0:3];
    real         max_skew     [0:3];
    int unsigned skew_errors;
    bit          exp_bit;
    bit          got_bit;

    task automatic check_bit();
        exp_bit = ref_next();
        got_bit = serial;
        if (exp_bit !== got_bit) begin
            errors++;
            phase_errors[bit_idx % 4]++;
            if (errors <= 20)
                $display("[ERR ] t=%0t ps ui=%0d phase=%0d exp=%b got=%b",
                         $time, bit_idx, bit_idx % 4, exp_bit, got_bit);
        end
        bit_idx++;
    endtask

    // ---- DDR sampler: posedge + negedge of the 1 GHz reference --------------
    initial begin
        bit_idx          = 0;
        errors           = 0;
        skew_errors      = 0;
        ref_state        = 31'h7FFF_FFFF;
        for (int p = 0; p < 4; p++) begin
            phase_errors[p] = 0;
            max_skew[p]     = 0.0;
        end
        rst_n            = 1'b0;
        enable           = 1'b1;
        clear            = 1'b0;

        #RST_AT rst_n = 1'b1;

        @(posedge clk_word);          // first PRBS nibble (seed) loads
        @(posedge clk_ph[0]);         // first UI starts here

        for (int unsigned i = 0; i < NBITS/2; i++) begin
            @(posedge clk_ref); check_bit();   // even UI (ph0/ph2 slots)
            @(negedge clk_ref); check_bit();   // odd  UI (ph1/ph3 slots)
        end
    end

    // ---- Transition timing / inter-phase skew monitor -----------------------
    real t_tr, dev, boundary;
    int  k_tr, ph_tr;
    always @(serial) begin
        t_tr = $realtime;
        if (t_tr >= BASE) begin
            k_tr = $rtoi((t_tr - BASE) / 0.5 + 0.5);   // nearest UI boundary
            boundary = BASE + 0.5 * k_tr;
            dev = t_tr - boundary;
            if (dev < 0.0) dev = -dev;
            ph_tr = k_tr % 4;
            if (dev > max_skew[ph_tr]) max_skew[ph_tr] = dev;
            if (dev > 0.05) skew_errors++;
        end
    end

    // ---- tx_gearbox_stage smoke test (8 -> 4) -------------------------------
    logic       gclk, gclk2, grst_n;
    logic [7:0] g_in;
    logic [3:0] g_out;
    int         g_errors, g_checked, exp_head, exp_tail;
    logic [3:0] exp_q [0:127];

    tx_gearbox_stage #(.N(4)) u_gb (
        .clk    (gclk),
        .clk2   (gclk2),
        .rst_n  (grst_n),
        .in_word(g_in),
        .out_word(g_out)
    );

    // gclk2: 500 MHz posedges at 1.0 + 2k; gclk: 250 MHz posedges at 2.0 + 4k
    // (halfway between gclk2 edges, per the stage's clock contract).
    initial begin
        gclk2 = 1'b0;
        forever begin
            #1.0 gclk2 = 1'b1;
            #1.0 gclk2 = 1'b0;
        end
    end
    initial begin
        gclk = 1'b0;
        #2.0;
        forever begin
            gclk = 1'b1; #2.0;
            gclk = 1'b0; #2.0;
        end
    end

    always @(posedge gclk) begin
        exp_q[exp_tail & 127] = g_in[3:0]; exp_tail++;
        exp_q[exp_tail & 127] = g_in[7:4]; exp_tail++;
    end
    always @(negedge gclk) g_in = g_in + 7'd1;   // drive between capture edges

    initial begin
        g_errors  = 0;
        g_checked = 0;
        exp_head  = 0;
        exp_tail  = 0;
        g_in      = 8'hA5;
        grst_n    = 1'b0;
        #0.5 grst_n = 1'b1;
        forever begin
            @(posedge gclk2);
            #0.25;   // settle after the select toggle
            if (exp_head < exp_tail) begin
                if (g_out !== exp_q[exp_head & 127]) begin
                    g_errors++;
                    if (g_errors <= 10)
                        $display("[GB ERR] t=%0t exp=%h got=%h",
                                 $time, exp_q[exp_head], g_out);
                end
                exp_head++;
                g_checked++;
            end
        end
    end

    // ---- Report --------------------------------------------------------------
    real worst, least, ip_skew;
    initial begin
        #500_000;
        $display("[FATAL] TB timeout");
        $finish;
    end

    // Report runs when the sampler loop finishes (ended by $finish there? no:
    // the report is triggered from a second process watching bit_idx).
    always @(bit_idx) begin
        if (bit_idx == NBITS) begin
            worst = 0.0; least = 1e9;
            for (int p = 0; p < 4; p++) begin
                if (max_skew[p] > worst) worst = max_skew[p];
                if (max_skew[p] < least) least = max_skew[p];
            end
            ip_skew = worst - least;
            $display("==================================================");
            $display("TB tx_parallel_mux / tx_prbs31_macro");
            $display("  bits checked        : %0d", bit_idx);
            $display("  bit errors          : %0d (BER %s)", errors,
                     (errors == 0) ? "= 0 (error-free)" : "> 0");
            $display("  per-phase errors    : ph0=%0d ph1=%0d ph2=%0d ph3=%0d",
                     phase_errors[0], phase_errors[1],
                     phase_errors[2], phase_errors[3]);
            $display("  max |edge dev| / ph : ph0=%.3f ps ph1=%.3f ps ph2=%.3f ps ph3=%.3f ps",
                     max_skew[0]*1000.0, max_skew[1]*1000.0,
                     max_skew[2]*1000.0, max_skew[3]*1000.0);
            $display("  inter-phase skew    : %.3f ps (ideal clocks)", ip_skew*1000.0);
            $display("  gearbox 8->4 check  : %0d nibbles, %0d errors",
                     g_checked, g_errors);
            if (errors == 0 && skew_errors == 0 && g_errors == 0)
                $display("  RESULT: PASS");
            else
                $display("  RESULT: FAIL");
            $display("==================================================");
            $finish;
        end
    end

endmodule
