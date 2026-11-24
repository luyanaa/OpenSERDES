`timescale 1ns/1ps
// -----------------------------------------------------------------------------
// Stage-B TX TB: PRBS31 TX macro on the REAL multiphase clock path.
//
// Two compile modes from this one file:
//
//  A) Functional (behavioral tx_clk4, ideal 4-phase gen):
//     iverilog -g2012 -DTX_CLK4_BEHAVIORAL -o build/tb_tx_prbs31_pma.vvp \
//       rtl/tx_prbs31_macro.sv rtl/tx_parallel_mux.sv pma/tx_clk4.sv \
//       pma/tx_driver.sv tb/tb_tx_prbs31_pma.sv
//     (ulimit -v 4000000; vvp build/tb_tx_prbs31_pma.vvp)
//
//  B) Structural (real PLL_MULTIPHASE_DCO path elaborates AND oscillates,
//     using TIMED stand-ins for the PLL_CELL_* wrappers -- the PDK cells are
//     zero-delay in digital sim, so the ring cannot self-oscillate without
//     them, same approach as adpll_gen src/pll_multiphase_dco_tb.v):
//     iverilog -g2012 -DTX_CLK4_STRUCT -o build/tb_tx_prbs31_pma_struct.vvp \
//       rtl/tx_prbs31_macro.sv rtl/tx_parallel_mux.sv pma/tx_clk4.sv \
//       pma/tx_driver.sv /grid/adpll_gen/src/pll_multiphase_dco.v \
//       tb/tb_tx_prbs31_pma.sv
//     (ulimit -v 4000000; vvp build/tb_tx_prbs31_pma_struct.vvp)
//
// Mode A checks that serial_out still reconstructs PRBS31 bit-exactly at the
// right times (checker approach reused from tb/tb_tx_parallel_mux.sv): DDR
// 1 GHz reference sampling, per-phase error count, per-phase edge deviation
// (inter-phase skew).  Mode B measures the real ring: gated = no oscillation,
// enabled = oscillation, DCO period, and the measured clk_ph phase offsets of
// BOTH mappings (even taps PHASE[0,2,4,6], tap pair PHASE[0]/PHASE[4]+comp)
// against the ideal 0/90/180/270 grid.
// -----------------------------------------------------------------------------
`ifdef TX_CLK4_STRUCT
// ---------------------------------------------------------------------------
// Timed sim stand-ins for the PLL_CELL_* PDK wrappers (structure-identical
// port lists, behavioral #delay bodies, TT-like 40 ps per stage).  Only the
// three cell types PLL_MULTIPHASE_DCO uses are needed.  Compile this mode
// WITH pll_multiphase_dco.v and WITHOUT any pll_cells_*.v.
// ---------------------------------------------------------------------------
module PLL_CELL_INVX1(input I, output ZN);
    assign #0.040 ZN = ~I;                       // sg13g2_inv_1 stand-in
endmodule
module PLL_CELL_BUFFX0(input I, output Z);
    assign #0.040 Z  = I;                        // sg13g2_buf_1 stand-in
endmodule
module PLL_CELL_NAND2X1(input A0, A1, output ZN);
    assign #0.040 ZN = ~(A0 & A1);               // sg13g2_nand2_1 stand-in
endmodule
module PLL_CELL_AND2X1(input A0, A1, output Z);
    assign #0.040 Z  = (A0 & A1);                // sg13g2_and2_1 stand-in
endmodule
`endif

module tb_tx_prbs31_pma;

`ifndef TX_CLK4_STRUCT
    // =========================================================================
    // MODE A: functional BER on the behavioral 4-phase clock
    // =========================================================================
    localparam int unsigned NBITS = 20000;
    localparam real         RST_AT = 6.3;    // reset deassert (ns)
    localparam real         BASE   = 8.5;    // first checked UI boundary (ns)

    logic        clk_word;
    logic        rst_n;
    logic        enable;
    logic        clear;
    logic        dco_en;
    logic        serial;
    logic        dv;
    logic [3:0]  nib_dbg;
    logic        clk_ref;                    // 1 GHz DDR sampling reference

    // clk_word: 500 MHz, posedge 250 ps before every clk_ph[0] posedge
    // (tx_clk4 behavioral puts the first ph0 posedge at 0.5 ns).
    initial begin
        clk_word = 1'b0;
        #0.25;
        forever begin
            clk_word = 1'b1; #1.0;
            clk_word = 1'b0; #1.0;
        end
    end

    tx_prbs31_macro #(
        .PRBS_SEED     (31'h7FFF_FFFF),
        .USE_REAL_CLOCK(1'b1)
    ) dut (
        .clk_word  (clk_word),
        .clk_ph    (4'b0),               // unused in real-clock mode
        .rst_n     (rst_n),
        .enable    (enable),
        .clear     (clear),
        .dco_en    (dco_en),
        .serial_out(serial),
        .data_valid(dv),
        .nibble_dbg(nib_dbg)
    );

    wire [3:0] ph_obs;                 // serializer phase clocks (observation)
    assign ph_obs = dut.ph_ser;

    // 1 GHz reference: one sample per 500 ps UI across both edges.
    initial begin
        clk_ref = 1'b0;
        #8.75;
        forever begin
            clk_ref = 1'b1; #0.5;
            clk_ref = 1'b0; #0.5;
        end
    end

    // ---- PRBS31 reference (same convention as rtl/prbs_gen.sv: MSB first) --
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

    // ---- DDR sampler -------------------------------------------------------
    initial begin
        bit_idx     = 0;
        errors      = 0;
        skew_errors = 0;
        ref_state   = 31'h7FFF_FFFF;
        for (int p = 0; p < 4; p++) begin
            phase_errors[p] = 0;
            max_skew[p]     = 0.0;
        end
        rst_n  = 1'b0;
        enable = 1'b1;
        clear  = 1'b0;
        dco_en = 1'b1;                 // behavioral gen ignores it; real path needs it

        #RST_AT rst_n = 1'b1;

        @(posedge clk_word);           // first PRBS nibble (seed) loads
        @(posedge ph_obs[0]);          // first UI starts here

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
            k_tr = $rtoi((t_tr - BASE) / 0.5 + 0.5);
            boundary = BASE + 0.5 * k_tr;
            dev = t_tr - boundary;
            if (dev < 0.0) dev = -dev;
            ph_tr = k_tr % 4;
            if (dev > max_skew[ph_tr]) max_skew[ph_tr] = dev;
            if (dev > 0.05) skew_errors++;
        end
    end

    // ---- Report --------------------------------------------------------------
    real worst, least, ip_skew;
    initial begin
        #500_000;
        $display("[FATAL] TB timeout");
        $finish;
    end

    always @(bit_idx) begin
        if (bit_idx == NBITS) begin
            worst = 0.0; least = 1e9;
            for (int p = 0; p < 4; p++) begin
                if (max_skew[p] > worst) worst = max_skew[p];
                if (max_skew[p] < least) least = max_skew[p];
            end
            ip_skew = worst - least;
            $display("==================================================");
            $display("TB tx_prbs31_pma (behavioral tx_clk4, real-clock wiring)");
            $display("  bits checked        : %0d", bit_idx);
            $display("  bit errors          : %0d (BER %s)", errors,
                     (errors == 0) ? "= 0 (error-free)" : "> 0");
            $display("  per-phase errors    : ph0=%0d ph1=%0d ph2=%0d ph3=%0d",
                     phase_errors[0], phase_errors[1],
                     phase_errors[2], phase_errors[3]);
            $display("  max |edge dev| / ph : ph0=%.3f ps ph1=%.3f ps ph2=%.3f ps ph3=%.3f ps",
                     max_skew[0]*1000.0, max_skew[1]*1000.0,
                     max_skew[2]*1000.0, max_skew[3]*1000.0);
            $display("  inter-phase skew    : %.3f ps (behavioral 4-phase)", ip_skew*1000.0);
            if (errors == 0 && skew_errors == 0)
                $display("  RESULT: PASS");
            else
                $display("  RESULT: FAIL");
            $display("==================================================");
            $finish;
        end
    end

`else
    // =========================================================================
    // MODE B: structural / oscillation check of the REAL PLL_MULTIPHASE_DCO
    // path (macro elaborated with real clocking + two tx_clk4 measurements,
    // one per phase mapping).
    // =========================================================================
    localparam real STAGE_DLY = 0.040;   // ns, matches the timed cells above
    localparam real TOL_PS    = 60.0;    // 1.5 tap stages @40 ps: 0.5-stage tap
                                         // quantization + observed duty asymmetry
                                         // of the behavioral timed cells; the
                                         // absolute quadrant error gets
                                         // re-measured on the routed macro

    logic        clk_word;               // present for the full macro path
    logic        rst_n, enable, clear, dco_en;
    logic        serial, dv;
    logic [3:0]  nibble_dbg;

    // Full macro, real clock inside: proves the REAL-clock path elaborates.
    tx_prbs31_macro #(
        .PRBS_SEED     (31'h7FFF_FFFF),
        .USE_REAL_CLOCK(1'b1)
    ) dut (
        .clk_word  (clk_word),
        .clk_ph    (4'b0),
        .rst_n     (rst_n),
        .enable    (enable),
        .clear     (clear),
        .dco_en    (dco_en),
        .serial_out(serial),
        .data_valid(dv),
        .nibble_dbg(nibble_dbg)
    );

    initial begin
        clk_word = 1'b0;
        #0.25;
        forever begin clk_word = 1'b1; #1.0; clk_word = 1'b0; #1.0; end
    end
    initial begin rst_n = 1'b0; enable = 1'b1; clear = 1'b0; dco_en = 1'b1; end

    // Three real clock instances:
    //   u_even / u_pair : released at 1.5 ns (after the first traversal has
    //                     defined chain[NSTAGE]) -- both phase mappings
    //                     measured on a running ring;
    //   u_late          : held gated until 20 ns (long after settle), then
    //                     released -- the travelling-wave ring (odd loop
    //                     inversion, AND closure) must START from any
    //                     release timing; the old even-inversion ring
    //                     latched here.
    // NOTE on sim X-waves: the initial X on chain[NSTAGE] is injected into
    // the loop when DCO_EN rises while that node is still X, and circulates
    // (nodes read X half of each period).  It is a zero-init artifact only:
    // the DEFINED edges stay on the exact 2-traversal schedule.  All edge
    // counters/monitors below therefore filter to clean 0->1 transitions.
    logic       en_burst, en_late;
    wire [3:0]  ph_even, ph_pair, ph_late;
    wire        dco_e, dco_p, dco_l;

    tx_clk4 #(.STEP(3), .CLOSE_LOAD(1), .USE_TAP_PAIR(1'b0)) u_even (
        .DCO_EN(en_burst), .clk_ph(ph_even), .dco_out(dco_e), .dco_out_n());
    tx_clk4 #(.STEP(3), .CLOSE_LOAD(1), .USE_TAP_PAIR(1'b1)) u_pair (
        .DCO_EN(en_burst), .clk_ph(ph_pair), .dco_out(dco_p), .dco_out_n());
    tx_clk4 #(.STEP(3), .CLOSE_LOAD(1), .USE_TAP_PAIR(1'b0)) u_late (
        .DCO_EN(en_late), .clk_ph(ph_late), .dco_out(dco_l), .dco_out_n());

    // clean 0->1 edge counters (X->1 does NOT count)
    logic e_prev_e = 1'bx, e_prev_p = 1'bx, e_prev_l = 1'bx;
    int unsigned e_cnt_e = 0, e_cnt_p = 0, e_cnt_l = 0;
    real dco_e_last = 0.0, dco_e_prevt = 0.0;
    always @(dco_e) begin
        if (dco_e === 1'b1 && e_prev_e === 1'b0) begin
            dco_e_prevt = dco_e_last;
            dco_e_last  = $realtime;
            e_cnt_e++;
        end
        e_prev_e = dco_e;
    end
    always @(dco_p) begin
        if (dco_p === 1'b1 && e_prev_p === 1'b0) e_cnt_p++;
        e_prev_p = dco_p;
    end
    always @(dco_l) begin
        if (dco_l === 1'b1 && e_prev_l === 1'b0) e_cnt_l++;
        e_prev_l = dco_l;
    end

    // ---- measurements -------------------------------------------------------
    real T_dco, t0;
    real cap_e [0:3], cap_p [0:3];
    real off_e [0:3], off_p [0:3];
    bit  cap_en = 1'b0;
    logic [3:0] prev_e = 4'bx, prev_p = 4'bx;
    real t_sync; bit sync_done = 1'b0;

    generate
        for (genvar g = 1; g < 4; g++) begin : g_mon   // g=0 syncs the window
            always @(ph_even[g]) begin
                if (cap_en && ph_even[g] === 1'b1 && prev_e[g] === 1'b0)
                    cap_e[g] = $realtime;
                prev_e[g] = ph_even[g];
            end
            always @(ph_pair[g]) begin
                if (cap_en && ph_pair[g] === 1'b1 && prev_p[g] === 1'b0)
                    cap_p[g] = $realtime;
                prev_p[g] = ph_pair[g];
            end
        end
    endgenerate
    // capture-window sync on a clean ph_even[0] posedge
    always @(ph_even[0]) begin
        if (ph_even[0] === 1'b1 && prev_e[0] === 1'b0) begin
            t_sync = $realtime; sync_done = 1'b1;
        end
        prev_e[0] = ph_even[0];
    end

    real dev_e, dev_p, worst_pair;
    int  n_warn, base_l, late_edges;
    bit  ok_gated, ok_run, ok_sustain, ok_period, ok_pair, ok_late;

    initial begin
        $display("---- TX real-clock structural check (timed cells, %0.0f ps/stage) ----",
                 STAGE_DLY*1000.0);
        rst_n = 1'b0; enable = 1'b0; clear = 1'b0; dco_en = 1'b1;
        en_burst = 1'b0; en_late = 1'b0;

        // 1) release u_even/u_pair at 1.5 ns (first traversal done at
        //    26*0.04 = 1.04 ns: chain[NSTAGE] defined, no X injected)
        #1.5 en_burst = 1'b1;

        // 2) gated check on u_late (init X->value edge excluded by baseline)
        #0.5;                           // t = 2 ns, startup fill settled
        base_l = e_cnt_l;
        #6;                             // t = 8 ns gated
        ok_gated = (e_cnt_l == base_l);
        $display("  gated DCO_EN=0 : %0d new edges 2..8 ns -> %s",
                 e_cnt_l - base_l, ok_gated ? "OK (held off)" : "FAIL (oscillates)");

        // 3) running ring (early release)
        #2;                             // t = 10 ns (~3 periods at T~2.08 ns)
        ok_run = (e_cnt_e >= 2);
        $display("  early release  : %0d clean edges by 10 ns -> %s",
                 e_cnt_e, ok_run ? "OK (oscillates)" : "FAIL (dead)");

        // 4) DCO period from the last two clean posedges: travelling wave ->
        //    TWO loop traversals per period, T = 2*(NSTAGE+1)*stage_dly.
        T_dco = dco_e_last - dco_e_prevt;
        ok_period = (T_dco > 1.5 && T_dco < 3.0);
        $display("  DCO period     : %.3f ns (%.0f MHz) [expected %.3f ns = 2 x 26 stages x %0.0f ps] -> %s",
                 T_dco, 1000.0/T_dco, 52*STAGE_DLY, STAGE_DLY*1000.0,
                 ok_period ? "OK (travelling wave)" : "FAIL");

        // 5) phase offsets of both mappings, < 1 period window
        wait (sync_done);
        #0.010;                        // clear the posedge timestep
        cap_en = 1'b1;
        t0 = t_sync;
        #(T_dco * 0.9);                // first clean posedge per phase only
        cap_en = 1'b0;
        for (int p = 0; p < 4; p++) begin
            off_e[p] = (p == 0) ? 0.0 : (cap_e[p] - t0);
            off_p[p] = (p == 0) ? 0.0 : (cap_p[p] - t0);
        end

        $display("  even-tap mapping clk_ph[i]=PHASE[2i] offsets (ideal k*T/4 = %0.0f/%0.0f/%0.0f ps):",
                 T_dco*250.0, T_dco*500.0, T_dco*750.0);
        n_warn = 0;
        for (int p = 1; p < 4; p++) begin
            dev_e = off_e[p] - T_dco*p/4.0;
            $display("    ph%0d: %0.1f ps = %0.1f deg (dev %+.1f ps)%s",
                     p, off_e[p]*1000.0, off_e[p]/T_dco*360.0, dev_e*1000.0,
                     ((dev_e < 0 ? -dev_e : dev_e) > TOL_PS/1000.0) ?
                     "  <-- NOT 90-deg (even taps are 2*STEP = 6 stages apart, T/4 = 13)" : "");
            if ((dev_e < 0 ? -dev_e : dev_e) > TOL_PS/1000.0) n_warn++;
        end
        if (n_warn == 0)
            $display("  even-tap mapping IS the 0/90/180/270 set -> keep as default.");
        else
            $display("  [WARN] even-tap mapping is NOT a 90-deg set; see pma/tx_clk4.sv header.");

        $display("  tap-pair mapping PHASE[0]/PHASE[4]+complement offsets:");
        worst_pair = 0.0;
        for (int p = 1; p < 4; p++) begin
            dev_p = off_p[p] - T_dco*p/4.0;
            if (dev_p < 0) dev_p = -dev_p;
            if (dev_p > worst_pair) worst_pair = dev_p;
            $display("    ph%0d: %0.1f ps = %0.1f deg (dev %+.1f ps)", p,
                     off_p[p]*1000.0, off_p[p]/T_dco*360.0, dev_p*1000.0);
        end
        ok_pair = (worst_pair <= TOL_PS/1000.0);
        $display("  tap-pair worst 90-deg deviation: %.1f ps (tol %.0f ps = 1.5 tap stages) -> %s",
                 worst_pair*1000.0, TOL_PS, ok_pair ? "OK" : "FAIL");
        // inter-phase GAP uniformity (what the mux actually feels):
        begin
            real g, gmin, gmax;
            gmin = 1e9; gmax = 0.0;
            for (int p = 0; p < 4; p++) begin
                g = (p == 0) ? (T_dco - off_p[3]) : (off_p[p] - off_p[p-1]);
                if (g < gmin) gmin = g;
                if (g > gmax) gmax = g;
            end
            $display("  tap-pair inter-phase gaps   : %.1f..%.1f ps (spread %.1f ps, ideal %.1f)",
                     gmin*1000.0, gmax*1000.0, (gmax-gmin)*1000.0, T_dco*250.0);
        end

        // 6) LATE release on u_late (t = 20 ns, long after settle): the
        //    travelling-wave ring has no DC-stable state and must start.
        #(20000.0 - $realtime);
        base_l = e_cnt_l;
        en_late = 1'b1;
        #10000;                         // +10 us (~4800 periods)
        late_edges = e_cnt_l - base_l;
        ok_late = (late_edges > 1000);
        $display("  late release   : %0d new clean edges 20 ns..10.02 us (released AFTER settle) -> %s",
                 late_edges, ok_late ?
                 "OK (starts from any release timing -- latch risk resolved)" :
                 "FAIL (dead)");

        // 7) sustained oscillation over a long window
        #5000;                          // +5 us
        ok_sustain = (e_cnt_e > 1000);
        $display("  sustained      : %0d clean edges of u_even by ~15 us -> %s",
                 e_cnt_e, ok_sustain ? "OK" : "FAIL");

        $display("==================================================");
        $display("TB tx_prbs31_pma structural (real PLL_MULTIPHASE_DCO path)");
        $display("  real-clock macro elaborated : YES (dut with USE_REAL_CLOCK=1)");
        if (ok_gated && ok_run && ok_sustain && ok_period && ok_pair && ok_late)
            $display("  RESULT: PASS");
        else
            $display("  RESULT: FAIL");
        $display("==================================================");
        $finish;
    end

    initial begin
        #100_000;
        $display("[FATAL] structural TB timeout");
        $finish;
    end
`endif

endmodule
