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
// Rate point (250 MHz quarter-rate / 1 Gb/s): recompile with
//     -DRX_RATE_MHZ=250            (and -Ptb_rx_parallel_pma.RATE_MHZ=250)
// The nominal 500 MHz (2 Gb/s) point needs no flag.  RATE_MHZ is the
// quarter-rate clock (ring) frequency: UI = 0.5 ns at 500, 1.0 ns at 250.
// It scales the data bit period, the behavioral PI grid (rx_clk8
// PERIOD_NS) AND the timed PLL_CELL stand-in delays (structural), so both
// compile modes stay self-consistent at either rate.
//
// What is verified (acceptance): the CDR LOCKS and tracks USING the
// real-clock path, and the recovered bit stream is checked CONTINUOUSLY
// against the PRBS31 source with a REAL bit-error-rate accounting:
//
//   * ONE-TIME alignment after lock: find the single global offset of the
//     rx stream in the tx PRBS31 sequence (correlate the first 128 rx bits
//     over all candidate offsets; PRBS31 windows are unique, so the first
//     zero-error candidate IS the offset -- no ambiguity, no search over
//     repeats).
//   * Then EVERY bit of the N_CHECK run is compared bit-by-bit against the
//     running reference.  NO re-alignment, NO SLIP_FORGIVE masking, NO
//     sub-segment probes.  A real link either keeps lock or it does not;
//     re-aligning per segment masks exactly the failures this check exists
//     to catch.
//   * Report: N bits checked, N errors => BER, plus the error RUN-LENGTH
//     distribution (a real channel impairment shows burst structure; a
//     bookkeeping artifact shows scattered singles).  +DEBUG prints the
//     first mismatches with rx index and the pi_code/pi_frac phase word
//     logged at that word.
//
// Source: NRZ with a controllable ppm offset (PPM > 0 = data slower),
// PRBS31 default.  PATTERN sweep (transition-density stress for CDR
// tracking + sampler):
//   PATTERN=0  PRBS31                       (default, ~50% transitions)
//   PATTERN=1  alternating 1010...          (max transition density)
//   PATTERN=2  long-no-transition: PRBS31 burst of 32 bits, then the last
//              bit HELD for 96 bits, repeating (stress: long run tracking)
//   PATTERN=3  6% transition density (2 data bits per 16, legacy pattern)
//
// Transitions are skewed +0.02 ns past the boundary (finite rise), so the
// on-time edge sample reads the old bit.  Constraint honored: N_TX covers
// every rx index the checker can touch (candidate offset + i < N_TX by
// construction; the checker never reads past either array).
// -----------------------------------------------------------------------------
`ifndef RX_RATE_MHZ
`define RX_RATE_MHZ 500
`endif

`ifdef RX_CLK8_STRUCT
// ---------------------------------------------------------------------------
// Timed sim stand-ins for the PLL_CELL_* PDK wrappers (structure-identical
// port lists, behavioral #delay bodies).  The delay is chosen so the trimmed
// ring lands on the NOMINAL period: T = 2*(NSTAGE+1)*t_cell = 2*26*t_cell =
// 2.000 ns at the 500 MHz rate point -> t_cell = 38.4615 ps (TT-like; models
// a CLOSE_LOAD-trimmed ring -- a free-running un-trimmed ring at 40 ps cells
// is +4% slow, beyond the loop's +-3.1%/window rate budget).  RX_RATE_MHZ
// scales the delay so the 250 MHz point rings at T = 4.000 ns.  Compile WITH
// pll_multiphase_dco.v and WITHOUT any pll_cells_*.v.
// ---------------------------------------------------------------------------
module PLL_CELL_INVX1 #(parameter real TD = 0.0384615 * 500.0 / `RX_RATE_MHZ)
                       (input I, output ZN);
    assign #TD ZN = ~I;                      // sg13g2_inv_1 stand-in
endmodule
module PLL_CELL_BUFFX0 #(parameter real TD = 0.0384615 * 500.0 / `RX_RATE_MHZ)
                        (input I, output Z);
    assign #TD Z  = I;                       // sg13g2_buf_1 stand-in
endmodule
module PLL_CELL_NAND2X1 #(parameter real TD = 0.0384615 * 500.0 / `RX_RATE_MHZ)
                         (input A0, A1, output ZN);
    assign #TD ZN = ~(A0 & A1);              // sg13g2_nand2_1 stand-in
endmodule
module PLL_CELL_AND2X1 #(parameter real TD = 0.0384615 * 500.0 / `RX_RATE_MHZ)
                        (input A0, A1, output Z);
    assign #TD Z  = (A0 & A1);               // sg13g2_and2_1 stand-in
endmodule
`endif

module tb_rx_parallel_pma #(
    parameter int  RATE_MHZ     = `RX_RATE_MHZ, // quarter-rate clock (ring) MHz; 500 = 2 Gb/s, 250 = 1 Gb/s
    parameter int  N_CHECK      = 4000,
    parameter real PPM_DEF      = 0.0,
    parameter int  PAT_DEF      = 0,
    parameter int  PH_OFF_DEF   = 0,
    parameter int  LOCK_BUDGET  = 40000         // clk_q cycles
);
    localparam int DATA_W = 16;
    // UI at the rate point: 2 Gb/s (RATE=500) -> 0.5 ns nominal.
    localparam real UI_NOM_NS = 250.0 / RATE_MHZ;
    // Quarter-rate clock period = 4 UI (the rx_clk8 PERIOD_NS).
    localparam real CLK_PERIOD_NS = 4.0 * UI_NOM_NS;
    // tx must cover every rx bit the checker can collect AND every tx index
    // the one-time alignment can name: candidate offset <= N_TX - N_CHECK
    // keeps cand + i < N_TX for all i < N_CHECK (prior bug: unbounded tx
    // reads past the generated array).  N_TX >= N_RX >= N_CHECK.
    localparam int N_TX   = N_CHECK + 16384;
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
    // PERIOD_NS follows the rate point (4 UI).
    rx_clk8 #(
        .PERIOD_NS (CLK_PERIOD_NS)
    ) u_clk8 (
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

    // Watchdog: never let the sim hang (scaled with the rate point so the
    // slower 250 MHz run -- 2x the ns per bit -- stays inside it).
    initial begin
        #(2000000.0 * 500.0 / RATE_MHZ);
        $display("FAIL: watchdog timeout at %0t", $realtime);
        $fatal(1);
    end

    // Ring enable: release after the first ring traversal (26 * t_cell =
    // half the ring period) so chain[NSTAGE] is defined -- no X-wave
    // injected into the loop (see pma/tx_clk4.sv sim note).  Behavioral
    // mode ignores DCO_EN.
    localparam real RING_TRAV_NS = 0.5 * CLK_PERIOD_NS; // half ring period
    initial begin
        dco_en = 1'b0;
        #(1.5 * RING_TRAV_NS);
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
    real period;      // bit period in ns (rate point + ppm), UI nominal
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
            period = UI_NOM_NS * (1.0 + ppm / 1000000.0);
            t0     = 10.0 + 0.125 * ph_off;
        end
    endtask

    // Source bits: PRBS31 default (x^31 + x^3 + 1).  PATTERN 1 = alternating
    // (max transition density); 2 = long-no-transition (32-bit PRBS burst,
    // then the last bit held 96 bits, repeating); 3 = 6% transition density
    // (2 data bits per 16).
    logic [N_TX-1:0] tx_bits;
    initial begin
        logic [30:0] state;
        logic        prb, burst_tail;
        int          blk;
        state = 31'h7fffffff;
        get_cfg;
        for (int i = 0; i < N_TX; i++) begin
            prb = state[30];
            state = {state[29:0], state[30] ^ state[27]};
            tx_bits[i] = prb;
            if (pattern == 1)
                tx_bits[i] = (i % 2 == 0);
            else if (pattern == 2) begin
                blk = i % 128;
                if (blk < 32) burst_tail = prb;      // tail of the burst
                tx_bits[i] = (blk < 32) ? prb : burst_tail;
            end
            else if (pattern == 3)
                tx_bits[i] = prb & (((i % 16) < 2) ? 1'b1 : 1'b0);
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
    // events (bookkeeping markers in CLK_FOLLOW mode -- reported as INFO
    // only, NEVER used to mask or split the continuous check) and the
    // phase word live at each collected word (mismatch forensics).
    logic [N_RX-1:0] rx_bits;
    logic [11:0]     pi_log [0:N_RX-1];  // {pi_code, pi_frac} per bit slot
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
                    if (rxcnt < N_RX) begin
                        rx_bits[rxcnt] = rx_word[i];
                        pi_log[rxcnt]  = {pi_code, pi_frac};
                    end
                    rxcnt = rxcnt + 1;
                end
            end
            if (rxcnt >= N_RX) done_c = 1;
        end
        $display("INFO: collected %0d bits, %0d pi-code steps, pi_code=%0d pi_frac=%0d freq_trim=%0d",
                 rxcnt, nsteps, pi_code, pi_frac, freq_trim);
    end

    // Lock + report + CONTINUOUS PRBS31 check.
    int errors, lock_wait;
    int err_hist  [0:32];
    int eh;

    // Error run-length buckets: b0 = singles(1), b1 = 2-3, b2 = 4-7,
    // b3 = 8-15, b4 = 16-31, b5 = 32+ (burst structure evidence).
    int run_hist [0:5];
    int cur_run;

    task bump_run;
        input int r;
        begin
            if      (r == 1)        run_hist[0] = run_hist[0] + 1;
            else if (r <= 3)        run_hist[1] = run_hist[1] + 1;
            else if (r <= 7)        run_hist[2] = run_hist[2] + 1;
            else if (r <= 15)       run_hist[3] = run_hist[3] + 1;
            else if (r <= 31)       run_hist[4] = run_hist[4] + 1;
            else                    run_hist[5] = run_hist[5] + 1;
        end
    endtask

    initial begin
        get_cfg;
        errors = 0; lock_wait = 0; cur_run = 0;
        for (eh = 0; eh < 32; eh++) err_hist[eh] = 0;
        for (eh = 0; eh < 6; eh++)  run_hist[eh] = 0;
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
            $display("FAIL: no lock PPM=%0.1f PATTERN=%0d PH_OFF=%0d RATE=%0d",
                     ppm, pattern, ph_off, RATE_MHZ);
            $fatal(1);
        end
        $display("INFO: locked after %0d clk_q cycles, pi_code=%0d pi_frac=%0d freq_trim=%0d",
                 lock_wait, pi_code, pi_frac, freq_trim);

        wait (rxcnt >= N_RX);
        #0.001;

        begin : continuous_check
            int cand, best_cand, best_err, errs, i;
            // ---- ONE-TIME alignment (never repeated) ------------------
            // Correlate the first 128 collected rx bits against every
            // candidate offset in the tx PRBS31 array.  A PRBS31 window is
            // unique within the generator period, so the first candidate
            // with a ZERO 128-bit correlate is THE offset; stop there.
            // Bounds: cand + 127 < cand + N_CHECK <= N_TX, so no read past
            // tx_bits; rx indices < 128 < N_RX.
            best_cand = -1; best_err = 1 << 30;
            cand = 0;
            while (cand + N_CHECK <= N_TX && best_err != 0) begin
                errs = 0;
                for (i = 0; i < 128; i++)
                    if (rx_bits[i] !== tx_bits[cand + i]) errs++;
                if (errs < best_err) begin best_err = errs; best_cand = cand; end
                cand = cand + 1;
            end
            if (best_cand < 0 || best_err != 0) begin
                $display("FAIL: no zero-error PRBS31 alignment (best offset %0d, %0d/128 correlate errors)",
                         best_cand, best_err);
                if (best_cand >= 0)
                    for (i = 0; i < 128; i++)
                        if (rx_bits[i] !== tx_bits[best_cand + i])
                            $display("FAILDBG i=%0d rx=%b exp=%b pi_code=%0d pi_frac=%0d",
                                     i, rx_bits[i], tx_bits[best_cand + i],
                                     pi_log[i][11:9], pi_log[i][8:1]);
                $fatal(1);
            end
            $display("INFO: one-time alignment: rx stream starts at tx PRBS31 offset %0d (128/128 bits exact)",
                     best_cand);

            // ---- CONTINUOUS bit-by-bit check (no mask, no re-align) ---
            // Every one of the N_CHECK bits must match the running
            // reference at the frozen offset.  Each mismatch is a counted
            // bit error; run lengths are histogrammed.
            for (i = 0; i < N_CHECK; i++) begin
                if (rx_bits[i] !== tx_bits[best_cand + i]) begin
                    errors++;
                    cur_run++;
                    err_hist[i / 256]++;
                    if (errors <= 8 && $test$plusargs("DEBUG"))
                        $display("DBGD idx=%0d rx=%b exp=%b pi_code=%0d pi_frac=%0d (t=%0t)",
                                 i, rx_bits[i], tx_bits[best_cand + i],
                                 pi_log[i][11:9], pi_log[i][8:1], $realtime);
                end else if (cur_run != 0) begin
                    bump_run(cur_run);
                    cur_run = 0;
                end
            end
            if (cur_run != 0) bump_run(cur_run);
        end

        if (lock_stale) begin
            $display("ERROR: lock_stale asserted");
            errors++;
        end

        for (eh = 0; eh < 32; eh++)
            if (err_hist[eh] != 0)
                $display("DBG errhist rx[%0d..%0d): %0d", eh*256, eh*256+256, err_hist[eh]);
        $display("INFO: error run-length distribution: singles(1)=%0d 2-3=%0d 4-7=%0d 8-15=%0d 16-31=%0d 32+=%0d",
                 run_hist[0], run_hist[1], run_hist[2], run_hist[3], run_hist[4], run_hist[5]);
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
        $display("INFO: behavioral mode -- PI scheduler on the nominal %0.1f ns grid (compile with pll_multiphase_dco.v for the real ring)", CLK_PERIOD_NS);
`endif
        $display("INFO: CLK_FOLLOW=1: ppm elasticity = continuously re-timed clk_q/clk_ph (breathing recovered clock); gearbox repairs disabled (rotator-mode mechanism)");

        if (errors != 0) begin
            $display("FAIL: %0d bit errors in %0d checked (BER %.3e), continuous unmasked check, PPM=%0.1f PATTERN=%0d PH_OFF=%0d RATE=%0d",
                     errors, N_CHECK, (errors * 1.0) / N_CHECK,
                     ppm, pattern, ph_off, RATE_MHZ);
            $fatal(1);
        end
        $display("PASS: %0d bits checked continuously, 0 errors (BER 0), %0d pi-code steps (diagnostic only, not masked), lock at %0d clk_q cycles, PPM=%0.1f PATTERN=%0d PH_OFF=%0d RATE=%0d",
                 N_CHECK, nsteps, lock_wait, ppm, pattern, ph_off, RATE_MHZ);
        $finish;
    end

endmodule
