`timescale 1ns/1fs
`default_nettype none
// -----------------------------------------------------------------------------
// Quarter-rate parallel RX testbench.
//
//   * PI-following clock generation (the Stage-B structure): the loop's
//     phase word r_q = {pi_code[2:0], pi_frac[7:0]} spans one 500 MHz clock
//     (4 UI).  The coarse field tap[2:1] moves the clock in 0.5 ns = 1 UI
//     steps (a step maps the data taps centre -> centre and shifts the
//     content +-1 UI: the slip events); the fractional field moves it in
//     0.25/256 ns steps (0.98 ps, the ppm tracking; tap[0] is unused - an
//     odd coarse step would land the data taps on bit boundaries).
//     clk_q rises at t = phase (mod 2 ns); clk_ph[k] rises at phase +
//     0.2407 + 0.25*k, so the data taps sit 0.02 ns before each eye centre
//     and the edge taps 0.02 ns before each transition.
//   * NRZ source at 2 Gb/s (UI = 0.5 ns nominal) with a controllable ppm
//     offset (PPM stretches the bit period: PPM > 0 = data slower =
//     duplicated bits at elastic events; PPM < 0 = data faster = skipped
//     bits) and pattern selection: PATTERN=0 full-density PRBS15,
//     PATTERN=3 = PRBS15 gated to two bits per 16 (~6% transition
//     density), same as tb_cdr_multiphase.  Transitions are skewed +0.02 ns
//     past the bit boundary (finite rise time), so the on-time edge sample
//     reads the old bit and the bang-bang vote flips only when the clock is
//     late past the transition.
//   * Check: lock within budget, then the word stream is compared against
//     the source.  Slip events split the stream into segments; each segment
//     is independently re-correlated (absorbs +-1-bit repair timing) and
//     bits within SLIP_FORGIVE of a slip are masked.  At PPM = 0 there are
//     no slips and the check is a strict global best-alignment with 0
//     errors required.
// -----------------------------------------------------------------------------
module tb_cdr_parallel #(
    parameter int  N_CHECK      = 4000,
    parameter real PPM_DEF      = 0.0,
    parameter int  PAT_DEF      = 0,
    parameter int  PH_OFF_DEF   = 0,
    parameter int  LOCK_BUDGET  = 20000,   // clk_q cycles
    parameter int  SLIP_FORGIVE = 64       // bits masked around each slip
);
    int DBG_EN;                            // plusarg DEBUG=1 enables DBG traces

    localparam int DATA_W = 16;
    localparam int N_TX   = N_CHECK + 1024;
    localparam int N_RX   = N_CHECK + 2048;

    logic        clk_q;
    logic [7:0]  clk_ph;
    logic        rst_n, enable, data_in;
    wire [DATA_W-1:0] rx_word;
    wire         word_valid, lock, lock_stale;
    wire         slip_evt;
    wire [1:0]   slip_drop, slip_insert;
    wire [2:0]   pi_code;
    wire signed [12:0] freq_trim;

    cdr_parallel_top #(
        .PH_W(3), .FREQ_FRAC(8), .DECIM(16), .KI(1), .KP_SHIFT(5),
        .LEAK_UI_BITS(12), .LOCK_EDGES(16), .LOCK_STALE_BITS(16),
        .FRAC_BITS(12), .DATA_W(DATA_W), .ROT_EN(1'b1)
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
        .freq_trim  (freq_trim)
    );

    // Watchdog: never let the sim hang.
    initial begin
        #1000000;
        $display("FAIL: watchdog timeout at %0t", $realtime);
        $fatal(1);
    end

    // Ideal 8-phase clock: phase k rises at n*2.0 + 1.1875 + 0.25*k ns.
    // clk_q rises at odd ns (first toggle at t=1), so the phase grid is
    // anchored to the same odd 2 ns grid: the eight phase edges of one
    // quarter-rate cycle sit at group_start + 0.1875 .. +1.9375 ns, entirely
    // inside the cycle, and the capture at the next clk_q edge takes exactly
    // the eight samples of that cycle.  The pi_code rotator in
    // cdr_parallel_top (ROT_EN=1) emulates the loop's phase walk by remapping
    // these fixed samples; the Stage-B alternative (pi_code+pi_frac re-timing
    // the clock itself, CLK_FOLLOW=1 in the loop) is implemented in the RTL
    // but its elastic-timing bookkeeping is an open item, see
    // docs/rx_parallel_spec.md.
    // Quarter-rate clock, 500 MHz (2 ns period, 4 UI).
    initial clk_q = 1'b0;
    always #1.0 clk_q = ~clk_q;

    genvar g;
    generate
        for (g = 0; g < 8; g = g + 1) begin : g_ph
            initial begin
                clk_ph[g] = 1'b0;
                #(1.1875 + 0.25 * g);
                clk_ph[g] = 1'b1;
                forever begin
                    #1.0; clk_ph[g] = 1'b0;
                    #1.0; clk_ph[g] = 1'b1;
                end
            end
        end
    endgenerate

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

    // NRZ driver: bit i spans [t0 + i*period, ...).  Transitions land exactly
    // on bit boundaries; the sampler grid offset (1.1875 ns) keeps every
    // sample 0.0625..0.1875 ns away from them.
    initial begin
        get_cfg;
        data_in = 1'b0;
        wait (rst_n);
        #(t0 - 5.0);   // rst_n releases at t = 5.0 ns (3 clk_q posedges)
        for (int i = 0; i < N_TX; i++) begin
            data_in = tx_bits[i];
            #(period);
        end
    end

    integer evcnt;   // event pulses seen same-edge (debug)
    integer evcnt2;  // event pulses seen one ns later (debug)
    initial begin
        evcnt = 0; evcnt2 = 0;
    end
    always @(posedge clk_q) if (slip_evt) evcnt = evcnt + 1;
    always @(posedge clk_q) begin #1; if (slip_evt) evcnt2 = evcnt2 + 1; end

    // Collection: stream words into rx_bits; record slip events.
    logic [N_RX-1:0] rx_bits;
    int              rxcnt;
    int              done_c;
    int              slip_pos  [0:127];
    int              slip_typ  [0:127];   // +1 dup, -1 skip
    int              nslips;

    initial begin
        rxcnt  = 0;
        nslips = 0;
        done_c = 0;
        wait (lock);
        while (!done_c) begin
            @(posedge clk_q);
            #1;
            if (slip_evt && nslips < 128) begin
                slip_pos[nslips] = rxcnt;
                slip_typ[nslips] = (slip_insert != 2'b00) ? -1 : 1;
                nslips = nslips + 1;
                if (DBG_EN)
                    $display("DBG slip %0d t=%0t pos=%0d ins=%0d drop=%0d",
                             nslips, $realtime, rxcnt, slip_insert, slip_drop);
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
        $display("INFO: collected %0d bits, %0d slip events, pi_code=%0d freq_trim=%0d",
                 rxcnt, nslips, pi_code, freq_trim);
    end

    // Lock + report + check.
    int errors, lock_wait, total_masked;
    int err_hist  [0:32];
    int eh;

    initial begin
        get_cfg;
        DBG_EN = 0;
        void'($value$plusargs("DEBUG=%d", DBG_EN));
        errors = 0; lock_wait = 0; total_masked = 0;
        rst_n  = 1'b0;
        enable = 1'b0;
        repeat (3) @(posedge clk_q);
        rst_n = 1'b1;
        repeat (2) @(posedge clk_q);
        enable = 1'b1;

        if (DBG_EN) begin : dbg_loop
            int j9;
            for (j9 = 0; j9 < 200; j9++) begin
                @(posedge clk_q); #1;
                if (j9 % 8 == 0)
                    $display("DBG L[%0d] t=%0t pi=%0d freq=%0d evcnt=%0d/%0d vv=%b",
                             j9, $realtime, pi_code, dut.u_loop.freq_q,
                             evcnt, evcnt2, dut.u_bbpd.vote_valid);
            end
        end

        while (!(lock === 1'b1) && lock_wait < LOCK_BUDGET) begin
            @(posedge clk_q); #1; lock_wait++;
        end
        if (!(lock === 1'b1)) begin
            $display("FAIL: no lock PPM=%0.1f PATTERN=%0d PH_OFF=%0d", ppm, pattern, ph_off);
            $fatal(1);
        end
        $display("INFO: locked after %0d clk_q cycles (%0.1f ns), pi_code=%0d freq_trim=%0d",
                 lock_wait, lock_wait * 2.0, pi_code, freq_trim);

        wait (rxcnt >= N_RX);
        #1;

        // Segmented check: segments split SLIP_FORGIVE around each slip; each
        // segment is correlated against the source independently so a +-1-bit
        // repair timing shift cannot propagate.  Segment 0 scans the full tx
        // range; later segments search +-96 bits around the previous offset
        // advanced by the segment start difference.
        begin : check_block
            int seg_start [0:130];
            int seg_end   [0:130];
            int nseg, s, k, cand, best_cand, best_err, errs, t;
            int gap_lo, gap_hi;
            for (eh = 0; eh < 32; eh++) err_hist[eh] = 0;
            nseg = 0;
            s = 0;
            seg_start[0] = 0;
            for (k = 0; k < nslips; k++) begin
                gap_lo = slip_pos[k] - SLIP_FORGIVE;
                gap_hi = slip_pos[k] + SLIP_FORGIVE;
                if (gap_lo > seg_start[nseg] + 32) begin
                    seg_end[nseg] = gap_lo;
                    nseg = nseg + 1;
                    seg_start[nseg] = gap_hi;
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
                    cand = s + (seg_start[k] - seg_start[k-1]) - 96;
                    while (cand <= s + (seg_start[k] - seg_start[k-1]) + 96
                           && cand < N_TX - 64) begin
                        errs = 0; t = 0;
                        while (t < 96 && seg_start[k] + t < seg_end[k] && cand + t < N_TX) begin
                            if (rx_bits[seg_start[k] + t] !== tx_bits[cand + t]) errs++;
                            t++;
                        end
                        if (t < 96) errs = 999;
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
                    end
                end
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
        if (DBG_EN) begin : dbg_diffs
            int t9, shown;
            shown = 0;
            for (t9 = 0; t9 < N_RX && shown < 12; t9++)
                if (t9 + 252 < N_TX && rx_bits[t9] !== tx_bits[t9+252]) begin
                    $display("DBGD rx[%0d]=%b tx[%0d]=%b mod16=%0d", t9, rx_bits[t9], t9+252, tx_bits[t9+252], t9%16);
                    shown++;
                end
        end

        // Effective loop update rate (nominal, by construction of DECIM).
        $display("INFO: loop update rate = 2e9 UI/s / DECIM %0d = %0d MHz (one update per %0d UI = %0d clk_q cycles)",
                 16, 2000 / 16, 16, 16 / 4);

        if (errors != 0) begin
            $display("FAIL: %0d bit errors outside slip neighborhoods, %0d slip events, PPM=%0.1f PATTERN=%0d PH_OFF=%0d",
                     errors, nslips, ppm, pattern, ph_off);
            $fatal(1);
        end
        $display("PASS: %0d bits checked, 0 errors, %0d slip events (masked %0d slip-adjacent bits), lock at %0d clk_q cycles, PPM=%0.1f PATTERN=%0d PH_OFF=%0d",
                 N_CHECK, nslips, total_masked, lock_wait, ppm, pattern, ph_off);
        $finish;
    end

endmodule
