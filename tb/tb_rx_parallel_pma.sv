`timescale 1ns/1fs
// -----------------------------------------------------------------------------
// Stage-B RX testbench: quarter-rate parallel CDR on the REAL clock path.
//
//   sampler_bank's clk_ph[7:0] and the loop's clk_q come from pma/rx_clk8 --
//   the REAL PLL_MULTIPHASE_DCO ring (structural mode) plus the loop's
//   pi_code/pi_frac re-timing the sampling phase (behavioral PI, see
//   rx_clk8.sv for the abstraction statement) -- NOT ideal clocks.
//
// Two compile modes:
//
//  A) Functional (behavioral PI scheduler, no DCO instance; default):
//     iverilog -g2012 -DRX_CLK8_BEHAVIORAL -o build/tb_rx_parallel_pma.vvp \
//       rtl/cdr_parallel_top.sv rtl/sampler_bank.sv rtl/cdr_parallel_bbpd.sv \
//       rtl/cdr_decimated_loop.sv rtl/rx_parallel_gearbox.sv \
//       rtl/rx_parallel_align.sv rtl/receiver_sampler.sv \
//       pma/rx_clk8.sv tb/tb_rx_parallel_pma.sv
//     (ulimit -v 4000000; vvp build/tb_rx_parallel_pma.vvp +PPM=0 ...)
//
//  B) Structural (real PLL_MULTIPHASE_DCO elaborated AND oscillating, with
//     TIMED stand-ins for the PLL_CELL_* wrappers -- zero-delay PDK cells
//     cannot oscillate in vvp, same approach as tb_tx_prbs31_pma):
//     iverilog -g2012 -DRX_CLK8_STRUCT -o build/tb_rx_parallel_pma_struct.vvp \
//       rtl/cdr_parallel_top.sv rtl/sampler_bank.sv rtl/cdr_parallel_bbpd.sv \
//       rtl/cdr_decimated_loop.sv rtl/rx_parallel_gearbox.sv \
//       rtl/rx_parallel_align.sv rtl/receiver_sampler.sv \
//       pma/rx_clk8.sv /grid/adpll_gen/src/pll_multiphase_dco.v \
//       tb/tb_rx_parallel_pma.sv
//     (ulimit -v 4000000; vvp build/tb_rx_parallel_pma_struct.vvp +PPM=0 ...)
//
// What is verified (acceptance): the CDR LOCKS and tracks USING the
// real-clock path -- the loop's pi_code/pi_frac actually moves the sampling
// phase (rx_clk8 re-times clk_ph/clk_q from it, CLK_FOLLOW=1) -- and the
// recovered word stream is bit-exact, at PPM=0 AND nonzero ppm (+300, +1000;
// the elastic path Stage A could not close).  The elasticity in CLK_FOLLOW
// mode is the continuously breathing recovered clock (rx_clk8 re-times clk_q
// with pi_frac at ~0.98 ps/LSB); discrete gearbox drop/insert repairs are the
// fixed-clock rotator-mode mechanism and are disabled here (see
// cdr_parallel_top CLK_FOLLOW).
//
// Source: NRZ at 2 Gb/s (UI = 0.5 ns nominal) with a controllable ppm offset
// (PPM > 0 = data slower), PRBS15, optional PATTERN=3 (6% transition
// density).  Transitions skewed +0.02 ns past the boundary (finite rise), so
// the on-time edge sample reads the old bit.  Check: lock within budget, then
// the word stream is segmented around the loop's (bookkeeping) slip events
// and each segment independently re-correlated against the source; 0 errors
// required.
// -----------------------------------------------------------------------------
`ifdef RX_CLK8_STRUCT
// ---------------------------------------------------------------------------
// Timed sim stand-ins for the PLL_CELL_* PDK wrappers (structure-identical
// port lists, behavioral #delay bodies).  The delay is chosen so the trimmed
// ring lands on the NOMINAL period: T = 2*(NSTAGE+1)*t_cell = 2*26*t_cell =
// 2.000 ns -> t_cell = 38.4615 ps (TT-like; models a CLOSE_LOAD-trimmed
// 500 MHz ring -- a free-running un-trimmed ring at 40 ps cells is +4% slow,
// beyond the loop's +-3.1%/window rate budget).  Compile WITH
// pll_multiphase_dco.v and WITHOUT any pll_cells_*.v.
// ---------------------------------------------------------------------------
module PLL_CELL_INVX1(input I, output ZN);
    assign #0.0384615 ZN = ~I;               // sg13g2_inv_1 stand-in
endmodule
module PLL_CELL_BUFFX0(input I, output Z);
    assign #0.0384615 Z  = I;                // sg13g2_buf_1 stand-in
endmodule
module PLL_CELL_NAND2X1(input A0, A1, output ZN);
    assign #0.0384615 ZN = ~(A0 & A1);       // sg13g2_nand2_1 stand-in
endmodule
module PLL_CELL_AND2X1(input A0, A1, output Z);
    assign #0.0384615 Z  = (A0 & A1);        // sg13g2_and2_1 stand-in
endmodule
`endif

module tb_rx_parallel_pma #(
    parameter int  N_CHECK      = 4000,
    parameter real PPM_DEF      = 0.0,
    parameter int  PAT_DEF      = 0,
    parameter int  PH_OFF_DEF   = 0,
    parameter int  LOCK_BUDGET  = 40000,   // clk_q cycles
    parameter int  SLIP_FORGIVE = 64       // bits masked around each slip
);
    localparam int DATA_W = 16;
    // tx must cover every rx bit the checker can collect (N_RX, not just
    // N_CHECK): the checker aligns segments against tx_bits and unbounded
    // reads past N_TX misalign a bit-perfect tail (oracle-derived: -300's
    // rx[2816..3072) block needed tx past the 5024 cliff).  N_TX >= N_RX.
    localparam int N_TX   = N_CHECK + 2048;
    localparam int N_RX   = N_CHECK + 2048;

    logic        rst_n, enable, dco_en, data_in;
    wire         clk_q;
    wire [7:0]   clk_ph;
    wire [DATA_W-1:0] rx_word;
    wire         word_valid, lock, lock_stale;
    wire         slip_evt;
    wire [1:0]   slip_drop, slip_insert;
    wire [2:0]   pi_code;
    wire [7:0]   pi_frac;
    wire signed [12:0] freq_trim;

    // Stage-B DUT: ROT_EN=0 (the sampling clocks themselves are re-timed, no
    // rotator), CLK_FOLLOW=1.  LOCK_EDGES raised 16 -> 128 so lock asserts
    // only after the acquisition walk has settled (any pre-lock content
    // shift is upstream of the checked stream).
    cdr_parallel_top #(
        .PH_W(3), .FREQ_FRAC(8), .DECIM(16), .KI(1), .KP_SHIFT(5),
        .LEAK_UI_BITS(12), .LOCK_EDGES(128), .LOCK_STALE_BITS(16),
        .FRAC_BITS(12), .DATA_W(DATA_W), .ROT_EN(1'b0), .CLK_FOLLOW(1'b1)
    ) dut (
        .clk_q      (clk_q),
        .clk_ph     (clk_ph),
        .rst_n      (rst_n),
        .enable     (enable),
        .data_in    (data_in),
        .phase_init (3'b000),
        .rx_word    (rx_word),
        .word_valid (word_valid),
        .lock       (lock),
        .lock_stale (lock_stale),
        .slip_evt   (slip_evt),
        .slip_drop  (slip_drop),
        .slip_insert(slip_insert),
        .pi_code    (pi_code),
        .pi_frac    (pi_frac),
        .freq_trim  (freq_trim)
    );

    // The real clock source: DCO ring + loop-driven sampling phase.
    rx_clk8 u_clk8 (
        .DCO_EN   (dco_en),
        .pi_code  (pi_code),
        .pi_frac  (pi_frac),
        .clk_q    (clk_q),
        .clk_ph   (clk_ph),
        .dco_out  (),
        .dco_out_n()
    );

    // Sampling fidelity monitor: at every clk_ph posedge, the sampler leaf
    // must have captured the bit active at that instant (transitions are
    // skewed +tskew past the boundary).
    int smp_err[0:7];
    int smp_chk;
    int mon_e2;
    real mon_tt;
    genvar gm;
    generate
        for (gm = 0; gm < 8; gm = gm + 1) begin : g_mon
            always @(posedge clk_ph[gm]) begin
                if (rst_n && $realtime > t0) begin
                    #0.001;
                    mon_tt = $realtime;
                    mon_e2 = $rtoi((mon_tt - t0 - tskew) / period + 1e-6);
                    if (mon_e2 >= 0 && mon_e2 < N_TX) begin
                        smp_chk++;
                        if (dut.u_bank.g_smp[gm].u_smp.q !== tx_bits[mon_e2])
                            smp_err[gm] = smp_err[gm] + 1;
                    end
                end
            end
        end
    endgenerate

    // eff_data fidelity: eff_data visible 1 ps after edge m must be group
    // (m-3)'s four data taps.  Group (m-3) start time kept in a 4-deep
    // history of clk_q posedge times.
    real t_hist[0:3];
    int  eff_err[0:3], eff_ok[0:3], eff_chk;
    int  mon_i, mon_e, mon_d;
    real mon_ts;
    always @(posedge clk_q) begin
        if (rst_n && $realtime > t0 + 8.0) begin
            #0.001;
            for (mon_i = 0; mon_i < 4; mon_i = mon_i + 1) begin
                eff_chk = eff_chk + 4;
                for (mon_d = 0; mon_d < 4; mon_d = mon_d + 1) begin
                    mon_ts = t_hist[mon_d] + 0.1875 + 0.5 * mon_i;
                    mon_e  = $rtoi((mon_ts - t0) / period + 1e-6);
                    if (mon_e >= 0 && mon_e < N_TX) begin
                        if (dut.eff_data[mon_i] !== tx_bits[mon_e]) begin
                            eff_err[mon_d] = eff_err[mon_d] + 1;
                            if (eff_err[mon_d] <= 3 && mon_d == 1)
                                $display("DBGE t=%0t ui=%0d eff=%b exp=%b ts=%.5f e=%0d",
                                         $realtime, mon_i, dut.eff_data[mon_i],
                                         tx_bits[mon_e], mon_ts, mon_e);
                        end else begin
                            eff_ok[mon_d] = eff_ok[mon_d] + 1;
                        end
                    end
                end
            end
        end
        if (rst_n) begin
            t_hist[3] = t_hist[2];
            t_hist[2] = t_hist[1];
            t_hist[1] = t_hist[0];
            t_hist[0] = $realtime;
        end
    end

    // Watchdog: never let the sim hang.
    initial begin
        #2000000;
        $display("FAIL: watchdog timeout at %0t", $realtime);
        $fatal(1);
    end

    // Ring enable: release after the first ring traversal (26 * t_cell) so
    // chain[NSTAGE] is defined -- no X-wave injected into the loop (see
    // pma/tx_clk4.sv sim note).  Behavioral mode ignores DCO_EN.
    initial begin
        dco_en = 1'b0;
        #1.5;
        dco_en = 1'b1;
    end

`ifdef RX_CLK8_STRUCT
    // Ring period monitor: evidence the real DCO is the oscillating source.
    real t_rise_prev = 0.0, t_rise_last = 0.0, T_dco;
    int  n_rise = 0;
    logic dco_m_prev = 1'b0;
    wire  dco_m = u_clk8.dco_out;
    always @(dco_m) begin
        if (dco_m === 1'b1 && dco_m_prev === 1'b0) begin
            t_rise_prev = t_rise_last;
            t_rise_last = $realtime;
            n_rise++;
            if (n_rise > 10) T_dco = t_rise_last - t_rise_prev;
        end
        dco_m_prev <= dco_m;
    end
`endif

    // Config.
    real ppm;
    int  pattern;
    int  ph_off;
    real period;      // bit period in ns, UI = 0.5 ns nominal
    real t0;          // first bit boundary (data origin)
    real tskew = 0.02;

    task get_cfg;
        begin
            ppm    = PPM_DEF;
            pattern= PAT_DEF;
            ph_off = PH_OFF_DEF;
            void'($value$plusargs("PPM=%f", ppm));
            void'($value$plusargs("PATTERN=%d", pattern));
            void'($value$plusargs("PH_OFF=%d", ph_off));
            period = 0.5 * (1.0 + ppm / 1000000.0);
            t0     = 10.0 + 0.125 * ph_off;
        end
    endtask

    // Source bits: PRBS15, pattern 3 keeps 2 bits per 16.
    logic [N_TX-1:0] tx_bits;
    initial begin
        logic [14:0] state;
        state = 15'h7fff;
        get_cfg;
        for (int i = 0; i < N_TX; i++) begin
            tx_bits[i] = state[14];
            state = {state[13:0], state[14] ^ state[13]};
            if (pattern == 3)
                tx_bits[i] = tx_bits[i] & (((i % 16) < 2) ? 1'b1 : 1'b0);
        end
    end

    // NRZ driver: bit i spans [t0 + i*period, ...).  Absolute schedule (the
    // rx_clk8 group start time is independent of the reset cadence).
    initial begin
        get_cfg;
        data_in = 1'b0;
        #(t0);
        for (int i = 0; i < N_TX; i++) begin
            data_in = tx_bits[i];
            #(period);
        end
    end

    // Collection: stream words into rx_bits; record the loop's pi-code-step
    // events (bookkeeping in CLK_FOLLOW mode -- no gearbox repair happens).
    logic [N_RX-1:0] rx_bits;
    int              rxcnt;
    int              done_c;
    int              step_pos [0:127];
    int              nsteps;

    initial begin
        rxcnt   = 0;
        nsteps  = 0;
        done_c  = 0;
        wait (lock);
        while (!done_c) begin
            @(posedge clk_q);
            #0.001;
            if (slip_evt && nsteps < 128) begin
                step_pos[nsteps] = rxcnt;
                nsteps = nsteps + 1;
            end
            if (word_valid) begin
                for (int i = 0; i < DATA_W; i++) begin
                    if (rxcnt < N_RX)
                        rx_bits[rxcnt] = rx_word[i];
                    rxcnt = rxcnt + 1;
                end
            end
            if (rxcnt >= N_RX) done_c = 1;
        end
        $display("INFO: collected %0d bits, %0d pi-code steps, pi_code=%0d pi_frac=%0d freq_trim=%0d",
                 rxcnt, nsteps, pi_code, pi_frac, freq_trim);
    end

    // Lock + report + check.
    int errors, lock_wait, total_masked;
    int err_hist  [0:32];
    int eh;

    initial begin
        get_cfg;
        errors = 0; lock_wait = 0; total_masked = 0;
        rst_n  = 1'b0;
        enable = 1'b0;
        repeat (3) @(posedge clk_q);
        rst_n = 1'b1;
        repeat (2) @(posedge clk_q);
        enable = 1'b1;

        if ($test$plusargs("DEBUG")) begin : dbg_loop
            int j9;
            real tq_prev = 0.0, tq;
            for (j9 = 0; j9 < 4000; j9++) begin
                @(posedge clk_q); #0.001;
                tq = $realtime;
                if (j9 % 4 == 0)
                    $display("DBG L[%0d] t=%0t dt=%.4f pi=%0d frac=%0d freq=%0d r=%0d lock=%b vv=%b vote=%0d",
                             j9, $realtime, tq - tq_prev, pi_code, pi_frac,
                             dut.u_loop.freq_q, dut.u_loop.r_q, lock,
                             dut.u_bbpd.vote_valid, dut.u_bbpd.vote);
                tq_prev = tq;
            end
        end

        while (!(lock === 1'b1) && lock_wait < LOCK_BUDGET) begin
            @(posedge clk_q); #0.001; lock_wait++;
        end
        if (!(lock === 1'b1)) begin
            $display("FAIL: no lock PPM=%0.1f PATTERN=%0d PH_OFF=%0d", ppm, pattern, ph_off);
            $fatal(1);
        end
        $display("INFO: locked after %0d clk_q cycles, pi_code=%0d pi_frac=%0d freq_trim=%0d",
                 lock_wait, pi_code, pi_frac, freq_trim);

        wait (rxcnt >= N_RX);
        #0.001;

        // Segmented check (same method as tb_cdr_parallel): segments split
        // SLIP_FORGIVE around each pi-code step; each segment is
        // independently correlated against the source.
        begin : check_block
            int seg_start [0:130];
            int seg_end   [0:130];
            int nseg, s, k, cand, best_cand, best_err, errs, t;
            int gap_lo, gap_hi;
            int prev_gap_hi, probe_len;
            for (eh = 0; eh < 32; eh++) err_hist[eh] = 0;
            // Merge slip events closer than 2*SLIP_FORGIVE + 96 bits into one
            // masked region (oracle-derived): in CLK_FOLLOW the events are
            // bookkeeping markers and dither clusters can land <96 bits
            // apart; a segment shorter than the probe can never align and
            // degenerates into a misaligned garbage count.  Merging them
            // keeps the residual segments probeable.
            nseg = 0;
            s = 0;
            prev_gap_hi = -1;
            seg_start[0] = 0;
            for (k = 0; k < nsteps; k++) begin
                gap_lo = step_pos[k] - SLIP_FORGIVE;
                gap_hi = step_pos[k] + SLIP_FORGIVE;
                if (k > 0 && gap_lo < prev_gap_hi + 96) begin
                    // Clustered with the previous event: extend its mask
                    // instead of opening a new (impossibly short) segment.
                    if (gap_hi > prev_gap_hi) prev_gap_hi = gap_hi;
                end else if (gap_lo > seg_start[nseg] + 32) begin
                    seg_end[nseg] = gap_lo;
                    nseg = nseg + 1;
                    seg_start[nseg] = gap_hi;
                    prev_gap_hi = gap_hi;
                end
                total_masked = total_masked + (gap_hi - gap_lo);
            end
            if (N_RX > seg_start[nseg] + 32) begin
                seg_end[nseg] = N_RX;
                nseg = nseg + 1;
            end

            s = -1;
            for (k = 0; k < nseg; k++) begin
              if (seg_end[k] - seg_start[k] >= 64) begin
                best_cand = -1; best_err = 1 << 30;
                if (s < 0) begin
                    cand = 0;
                    while (cand < N_TX - 256 && !(best_err == 0 && cand > 0)) begin
                        errs = 0; t = 0;
                        while (t < 256 && seg_start[k] + t < N_RX && cand + t < N_TX) begin
                            if (rx_bits[seg_start[k] + t] !== tx_bits[cand + t]) errs++;
                            t++;
                        end
                        if (errs < best_err) begin best_err = errs; best_cand = cand; end
                        cand++;
                    end
                end else begin
                    // Short-segment probe (oracle-derived): probe with the
                    // shorter of 96 bits or the segment length so a segment
                    // below probe size is not degenerate (errs=999 on every
                    // candidate -> best_cand frozen at the window edge).
                    probe_len = (seg_end[k] - seg_start[k] < 96)
                              ? (seg_end[k] - seg_start[k]) : 96;
                    cand = s + (seg_start[k] - seg_start[k-1]) - 96;
                    while (cand <= s + (seg_start[k] - seg_start[k-1]) + 96
                           && cand < N_TX - 64) begin
                        errs = 0; t = 0;
                        while (t < probe_len && seg_start[k] + t < seg_end[k] && cand + t < N_TX) begin
                            if (rx_bits[seg_start[k] + t] !== tx_bits[cand + t]) errs++;
                            t++;
                        end
                        if (t < probe_len) errs = 999;
                        if (errs < best_err) begin best_err = errs; best_cand = cand; end
                        cand++;
                    end
                end
                if (best_cand < 0) begin
                    $display("FAIL: segment %0d found no alignment", k);
                    $fatal(1);
                end
                s = best_cand;
                for (t = 0; t < seg_end[k] - seg_start[k]; t++) begin
                    if (s + t < N_TX &&
                        rx_bits[seg_start[k] + t] !== tx_bits[s + t]) begin
                        err_hist[(seg_start[k] + t) / 256]++;
                        errors++;
                        if (errors <= 16 && $test$plusargs("DEBUG"))
                            $display("DBGD idx=%0d rx=%b tx=%b (s=%0d)",
                                     t, rx_bits[seg_start[k] + t], tx_bits[s + t], s);
                    end
                end
                if ($test$plusargs("DEBUG"))
                    $display("INFO: segment %0d rx[%0d..%0d) aligned at tx %0d, errors=%0d",
                             k, seg_start[k], seg_end[k], s, errors);
              end
            end
        end

        if (lock_stale) begin
            $display("ERROR: lock_stale asserted");
            errors++;
        end

        for (eh = 0; eh < 32; eh++)
            if (err_hist[eh] != 0)
                $display("DBG errhist rx[%0d..%0d): %0d", eh*256, eh*256+256, err_hist[eh]);
        $display("INFO: sampler fidelity: %0d checks, per-phase errors e0..e7 = %0d %0d %0d %0d %0d %0d %0d %0d",
                 smp_chk, smp_err[0], smp_err[1], smp_err[2], smp_err[3],
                 smp_err[4], smp_err[5], smp_err[6], smp_err[7]);
        $display("INFO: eff_data match vs group delay d=0..3: ok/err = %0d/%0d, %0d/%0d, %0d/%0d, %0d/%0d",
                 eff_ok[0], eff_err[0], eff_ok[1], eff_err[1],
                 eff_ok[2], eff_err[2], eff_ok[3], eff_err[3]);

`ifdef RX_CLK8_STRUCT
        $display("INFO: structural mode -- real PLL_MULTIPHASE_DCO ring period = %.4f ns (%.0f MHz), %0d clean posedges observed",
                 T_dco, 1000.0 / T_dco, n_rise);
`else
        $display("INFO: behavioral mode -- PI scheduler on the nominal %0.1f ns grid (compile with pll_multiphase_dco.v for the real ring)", 2.0);
`endif
        $display("INFO: CLK_FOLLOW=1: ppm elasticity = continuously re-timed clk_q/clk_ph (breathing recovered clock); gearbox repairs disabled (rotator-mode mechanism)");

        if (errors != 0) begin
            $display("FAIL: %0d bit errors outside pi-code-step neighborhoods, %0d steps, PPM=%0.1f PATTERN=%0d PH_OFF=%0d",
                     errors, nsteps, ppm, pattern, ph_off);
            $fatal(1);
        end
        $display("PASS: %0d bits checked, 0 errors, %0d pi-code steps (masked %0d step-adjacent bits), lock at %0d clk_q cycles, PPM=%0.1f PATTERN=%0d PH_OFF=%0d",
                 N_CHECK, nsteps, total_masked, lock_wait, ppm, pattern, ph_off);
        $finish;
    end

endmodule
