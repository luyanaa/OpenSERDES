`timescale 1ns/1ps
// 4x bang-bang CDR: Alexander BBPD + 2nd-order digital loop filter +
// fractional NCO.
//
// Structure
//   - cdr_sync oversamples the line; phase_q is a fractional phase
//     accumulator that advances by inc_v clock periods per clock and wraps
//     once per UI (ONE = OVERSAMPLE clock periods).
//   - The PD and the loop filter are registered: the vote sampled at a data
//     centre updates freq_q (integral) and emits a one-clock kick_q
//     (proportional).  Keeping them out of the always_comb removes the
//     combinational loop vote -> kick -> phase_next -> center_hit -> vote.
//   - inc_v is monotonic (clamped to [0.25, 1.25] clock periods), so phase_q
//     never moves backwards.  This matters: subtracting the proportional
//     term from phase_w after a wrap could push the phase just below the UI
//     boundary when phase_w < KP, and the next clock would wrap a second
//     time - a runt UI, a forced edge and a duplicate strobe.
//
// Event generation
//   edge_e   = OVERSAMPLE/2 clock periods (the line boundary)
//   center_e = OVERSAMPLE/2 + phase_offset (mod ONE) (the data sample)
// The loop closes on the edge sample, so the absolute phase origin - and
// therefore the SYNC_STAGES delay - is absorbed; only the edge-to-centre
// separation (half a UI) matters.  edge_e is far enough from the wrap that a
// wrapping clock (phase_q > 2.75 periods) can never also cross it, so there
// is exactly one edge per UI for every phase_offset.  center_e can coincide
// with the wrap (phase_offset = 0); that case is detected by an explicit
// next-UI crossing term so the ending UI's done flag cannot suppress it.
// Per-UI done flags bound the event count at one centre and one edge per UI;
// a monotonic accumulator advancing by less than one clock period bounds it
// from below.
module cdr_bang_bang #(
    parameter int OVERSAMPLE      = 4,
    parameter int SYNC_STAGES     = 3,
    // Fractional bits of the NCO: the frequency resolution of the integral
    // path, 1 LSB = 2^-NCO_FRAC clock periods per clock (15 ppm at 16).  20 is
    // over-provisioned - it costs extra Yosys cells in the signed loop
    // arithmetic for a resolution the PD can never use.  16 keeps the
    // quantisation drift below 0.05 UI per 10^4 UIs; 14 does not (0.2 UI).
    // Before the exact-width change the module was 1489 cells at 20, 1285 at
    // 16, 1191 at 14; after it, 573 at 16.  See
    // docs/bang_bang_pipeline_study.md for the width/timing analysis.
    parameter int NCO_FRAC        = 16,

    parameter int KI              = 1,
    parameter int KP_SHIFT        = 5,
    // Leak on the integral path: one LSB per 2^LEAK_UI_BITS *consecutive*
    // UIs that carried no transition.  The counter is cleared by a
    // transition, so this is a vote-drought timer, not absolute time.
    // LEAK_UI_BITS = 0 leaks every UI; a negative value disables the leak.
    parameter int LEAK_UI_BITS    = 12,
    parameter int LOCK_EDGES      = 16,
    parameter int LOCK_STALE_BITS = 16,
    parameter bit CLK_OUT_ENABLE  = 1'b1,
    parameter int PHASE_CTRL_W    = 2,
    parameter int FRAC_BITS       = 12
) (
    input  logic                      clk,
    input  logic                      rst_n,
    input  logic                      enable,
    input  logic                      flush,
    input  logic                      data_in,
    input  logic [PHASE_CTRL_W-1:0]   phase_offset,
    output logic                      data_out,
    output logic                      sample_strobe,
    output logic                      clk_out,
    output logic                      lock,
    output logic                      lock_stale,
    // Two's-complement loop state in NCO LSBs.  The port is deliberately not
    // declared signed: Yosys emits the keyword into the netlist and OpenROAD's
    // read_verilog rejects it, which blocks STA for every design that
    // instantiates this module.  Consumers wanting a signed value declare their
    // own variable as signed, as the testbenches do.
    output logic [FRAC_BITS:0] freq_trim
);
    localparam int OS      = (OVERSAMPLE < 4) ? 4 : OVERSAMPLE;
    localparam int PH_W    = $clog2(OS);
    localparam int N       = (SYNC_STAGES < 3) ? 3 : SYNC_STAGES;
    localparam int FR      = NCO_FRAC;
    // Exact widths instead of the old FR+PH_W+3 / FR+4 margins:
    //   phase_q in [0, 2^(FR+PH_W))   -> PWQ bits
    //   phase_next < 2^(FR+PH_W+1)    -> PWN = PWQ+1; bit PWQ is the wrap
    //   freq_q in [-2^(FR-2), 2^(FR-2)] -> FW = FR+2 signed
    // The old PW/FW margins cost one comparand's worth of carry chain each on
    // the single-cycle loop path (see docs/bang_bang_pipeline_study.md).
    localparam int PWQ     = FR + PH_W;
    localparam int PWN     = PWQ + 1;
    localparam int FW      = FR + 2;
    localparam logic [PWQ-1:0] CENTER = PWQ'(1 << (PWQ - 1));
    localparam logic [PWQ-1:0] CLKINC = PWQ'(1 << FR);
    localparam logic signed [FW-1:0] KP   = FW'(1 << (FR - KP_SHIFT));
    localparam logic signed [FW-1:0] KI_S = FW'(KI);
    localparam logic signed [FW-1:0] FMAX = FW'(1 << (FR - 2));
    localparam logic signed [FW-1:0] FMIN = -FW'(1 << (FR - 2));
    localparam int LOCK_W  = (LOCK_EDGES <= 1) ? 1 : $clog2(LOCK_EDGES + 1);
    localparam int STALE_W = (LOCK_STALE_BITS <= 1) ? 1 : $clog2(LOCK_STALE_BITS + 1);
    localparam bit LEAK_ON        = (LEAK_UI_BITS >= 0);
    localparam bit LEAK_EVERY_UI  = (LEAK_UI_BITS == 0);
    // The counter is LEAK_UI_BITS wide, so it counts 0..2^LEAK_UI_BITS-1 and
    // fires on the 2^LEAK_UI_BITS-th consecutive quiet UI.  Using a raw
    // count comparison here made LEAK_UI_BITS=12 mean 13 UIs, not 4096.
    localparam int LEAK_W  = (LEAK_UI_BITS < 1) ? 1 : LEAK_UI_BITS;
    localparam logic [LOCK_W-1:0]  LOCK_MAX  = LOCK_W'(LOCK_EDGES);
    localparam logic [STALE_W-1:0] STALE_MAX = STALE_W'(LOCK_STALE_BITS);
    localparam logic [LEAK_W-1:0]  LEAK_MAX  = {LEAK_W{1'b1}};

    logic [N-1:0]             samples;
    logic                     s;
    logic [PWQ-1:0]           phase_q;
    logic signed [FW-1:0]     freq_q;
    logic signed [FW-1:0]     kick_q;
    logic signed [FW-1:0]     delta;
    logic [PWN-1:0]           inc_c;   // control path (freq_q/kick_q)
    logic [PWN-1:0]           inc_r;   // NCO control word, registered
    logic signed [FW-1:0]     freq_next;
    logic [PWN-1:0]           phase_next;
    logic [PWQ-1:0]           phase_w;
    logic [PWQ-1:0]           center_e;
    logic [PWQ-1:0]           edge_e;
    logic                     wrap;
    logic                     cx_c;
    logic                     cx_e;
    logic                     nxt_c;
    logic                     nxt_e;
    // Threshold tests on the accumulator high bits.  center_e is
    // phase_offset << FR and edge_e is 2^(PWQ-1), both aligned to FR, so
    // phase_q/phase_w/phase_next compare against them with PWQ-FR (or +1) bits
    // instead of a full-width comparator.  Exact for phase_offset < OS, the
    // documented domain (phase_offset walks the eye within one UI).
    localparam int HI_W = PWQ - FR;            // == PH_W
    logic [HI_W-1:0]  pq_hi, pw_hi;
    logic [HI_W:0]    pn_hi;
    logic                     center_hit;
    logic                     edge_hit;
    logic                     c_done_q;
    logic                     e_done_q;
    logic signed [1:0]        vote;
    logic                     trans;
    logic                     d_q;
    logic                     d_prev_q;
    logic                     e_q;
    logic                     d_out_q;
    logic                     strobe_q;
    logic                     clk_hi_q;
    logic [LOCK_W-1:0]        edge_cnt_q;
    logic [STALE_W-1:0]       stale_cnt_q;
    logic [LEAK_W-1:0]        leak_cnt_q;
    logic                     trans_ui_q;
    logic                     lock_q;
    logic                     stale_q;

    cdr_sync #(.STAGES(N)) u_sync (
        .clk(clk), .rst_n(rst_n), .async_in(data_in), .samples(samples));

    assign s = samples[N-1];

    // ------------------------------------------------------------------
    // Combinational NCO and event generation.  No loop-filter term here:
    // kick_q and freq_q are registers, so this block only reads state.
    // ------------------------------------------------------------------
    always_comb begin
        // inc_v = CLKINC + freq_q - kick_q, clamped to [CLKINC/4, CLKINC+FMAX].
        // The lower bound is unreachable: freq_q >= FMIN and |kick_q| <= KP, so
        // inc_v >= CLKINC + FMIN - KP = 0.72*CLKINC > CLKINC/4.  Clamping the
        // signed delta alone is therefore equivalent and needs only a 16-bit
        // compare instead of two 20-bit ones on the loop path.
        delta = freq_q - kick_q;
        if (delta > FMAX) delta = FMAX;
        inc_c = PWN'(CLKINC) + PWN'(delta);

        // phase_next carries one extra bit: bit PWQ is the wrap, and the low
        // PWQ bits equal phase_next - ONE when it wrapped.  The wrap is a bit
        // test and the wrap subtraction is a truncation, no comparator or mux.
        // Fast path: phase += the registered NCO control word, so freq_q
        // is not in this loop.  inc_r is produced by the control path.
        phase_next = PWN'(phase_q) + PWN'(inc_r);
        wrap       = phase_next[PWQ];
        phase_w    = phase_next[PWQ-1:0];
        pq_hi      = phase_q[PWQ-1:FR];
        pw_hi      = phase_w[PWQ-1:FR];
        pn_hi      = phase_next[PWQ:FR];

        // Event positions.  The loop closes on the edge sample, so the
        // absolute phase origin (and therefore the sync delay) is absorbed;
        // only the edge-to-centre separation matters: it is exactly OS/2
        // clock periods, i.e. half a UI.
        //
        // edge_e = OS/2 clock periods.  A wrapping clock always starts from
        // phase_q > ONE - inc_max >= 2.75 periods, and the increment is at
        // most 1.25 periods, so the edge threshold is never crossed in the
        // same clock as a wrap: there is exactly one edge event per UI for
        // every phase_offset.
        //
        // center_e = OS/2 + OS/2 + phase_offset = phase_offset (mod ONE).
        // phase_offset = 0 samples the eye centre, OS/2 lands on the bit
        // boundary (unusable), and the values in between walk the sample
        // across the eye.  A centre threshold of 0 coincides with the wrap
        // and is detected by the "next UI" branch below.
        edge_e   = CENTER;
        // CENTER + CENTER == ONE and (x + ONE) & (ONE-1) == x for x < ONE, so
        // the old add/multiply/mask reduces to phase_offset * CLKINC (a shift;
        // phase_offset <= 3 so 3*CLKINC still fits in PWQ bits).
        center_e = PWQ'(phase_offset) * CLKINC;

        // Wrap-aware fractional threshold crossing.  On a wrap the traversed
        // interval is (phase_q, ONE) - the UI that is ending - followed by
        // [0, phase_w] - the UI that is starting.  cx_* is the ending UI's
        // crossing and nxt_* the starting UI's.  Keeping them apart matters:
        // the done flags belong to the ending UI, so an event that belongs to
        // the starting UI must not be suppressed by them.  Losing that
        // distinction dropped one centre event whenever phase_w happened to
        // land past center_e, and the edge then latched the previous bit.
        if (wrap) begin
            cx_c  = pq_hi < phase_offset;
            nxt_c = pw_hi >= phase_offset;
            cx_e  = !pq_hi[HI_W-1];
            nxt_e = pw_hi[HI_W-1];
            center_hit = (cx_c && !c_done_q) || nxt_c;
            edge_hit   = (cx_e && !e_done_q) || nxt_e;
        end else begin
            // phase_w == phase_next here (no wrap), but comparing phase_next
            // lets abc reuse the adder carry chain; the truncated phase_w
            // slice mapped 4 ns slower on sky130 (see the study doc).
            cx_c  = (pq_hi < phase_offset) && (pn_hi >= phase_offset);
            cx_e  = !pq_hi[HI_W-1] && pn_hi[HI_W-1];
            nxt_c = 1'b0;
            nxt_e = 1'b0;
            center_hit = !c_done_q && cx_c;
            edge_hit   = !e_done_q && cx_e;
        end
    end

    // Alexander PD, evaluated on registers only.  With a data transition,
    // e == d_prev means the edge sample still carries the old bit, i.e. the
    // clock is early (+1); otherwise it is late (-1).  An early vote has to
    // delay, hence freq -= KI*v and inc -= KP*v below.
    assign trans = center_hit && (s != d_prev_q);
    always_comb begin
        vote = 2'sd0;
        if (trans) vote = (e_q == d_prev_q) ? 2'sd1 : -2'sd1;
    end

    always_comb begin
        // KI == 1 makes KI*vote a value in {-1,0,+1}: an incrementer instead of
        // a 16-bit multiply/adder.  The general form stays for other KI values.
        // (Pre-computing both +/-1 candidates was tried: it doubles freq_q's
        // fanout and mapped 5 ns slower before placement, so keep one adder.)
        if (KI == 1)
            freq_next = freq_q + ((vote == 2'sd1) ? -FW'(1) :
                                  (vote == -2'sd1) ? FW'(1) : FW'(0));
        else
            freq_next = freq_q - (KI_S * vote);
        if (freq_next > FMAX) freq_next = FMAX;
        else if (freq_next < FMIN) freq_next = FMIN;
    end

    assign data_out      = d_out_q;
    assign sample_strobe = strobe_q;
    assign clk_out       = CLK_OUT_ENABLE ? clk_hi_q : 1'b0;
    assign lock          = lock_q;
    assign lock_stale    = stale_q;
    // Observation port only: FRAC_BITS+1 bits of a 2^NCO_FRAC-range value, so
    // it saturates (silently wraps) for |freq_q| >= 2^FRAC_BITS, i.e. beyond
    // 2^12 x 2^-NCO_FRAC clock periods per clock.  It is a loop-state probe in
    // NCO LSBs, not a calibrated ppm readout.
    assign freq_trim     = freq_q[FRAC_BITS:0];


    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            phase_q <= '0; freq_q <= '0; kick_q <= '0;
            inc_r <= PWN'(CLKINC);
            d_q <= 1'b0; d_prev_q <= 1'b0; e_q <= 1'b0; d_out_q <= 1'b0;
            strobe_q <= 1'b0; clk_hi_q <= 1'b0;
            c_done_q <= 1'b0; e_done_q <= 1'b0; leak_cnt_q <= '0;
            trans_ui_q <= 1'b0;
            edge_cnt_q <= '0; stale_cnt_q <= '0; lock_q <= 1'b0; stale_q <= 1'b0;
        end else if (flush) begin
            phase_q <= '0; freq_q <= '0; kick_q <= '0;
            inc_r <= PWN'(CLKINC);
            d_q <= 1'b0; d_prev_q <= 1'b0; e_q <= 1'b0; d_out_q <= 1'b0;
            strobe_q <= 1'b0; clk_hi_q <= 1'b0;
            c_done_q <= 1'b0; e_done_q <= 1'b0; leak_cnt_q <= '0;
            trans_ui_q <= 1'b0;
            edge_cnt_q <= '0; stale_cnt_q <= '0; lock_q <= 1'b0; stale_q <= 1'b0;
        end else if (enable) begin
            inc_r   <= inc_c;
            phase_q <= phase_w;
            strobe_q <= 1'b0;
            // The proportional kick is a single-clock increment offset, so it
            // must not repeat on the following cycles.
            kick_q <= '0;

            if (center_hit) begin
                d_q      <= s;
                d_prev_q <= s;
                c_done_q <= 1'b1;
                if (trans) begin
                    freq_q      <= freq_next;
                    kick_q      <= KP * vote;
                    trans_ui_q  <= 1'b1;
                    stale_cnt_q <= '0;
                    stale_q     <= 1'b0;
                    if (edge_cnt_q != LOCK_MAX)
                        edge_cnt_q <= edge_cnt_q + 1'b1;
                end
            end

            if (edge_hit) begin
                e_q      <= s;
                d_out_q  <= d_q;
                strobe_q <= 1'b1;
                e_done_q <= 1'b1;
                if (!trans) begin
                    if (stale_cnt_q != STALE_MAX)
                        stale_cnt_q <= stale_cnt_q + 1'b1;
                    if ((stale_cnt_q + 1'b1) >= STALE_MAX)
                        stale_q <= 1'b1;
                end
            end

            if (wrap) begin
                // Slow leak on the integral path: one LSB per 2^LEAK_UI_BITS
                // consecutive UIs with no transition, so the rate is set by
                // the parameter and not by the transition density; the old
                // per-sample leak removed one LSB on every non-transition sample,
                // which for PRBS is ~0.5 LSB per UI -
                // the same order as the KI=1 update, so it erased the
                // integral path and the loop ran on the proportional term
                // alone (freq_trim stayed at 0 even at +700 ppm).
                if (trans_ui_q || trans) begin
                    trans_ui_q <= 1'b0;
                    leak_cnt_q <= '0;
                end else if (LEAK_ON) begin
                    if (LEAK_EVERY_UI || leak_cnt_q == LEAK_MAX) begin
                        leak_cnt_q <= '0;
                        if (freq_q > 0)      freq_q <= freq_q - 1'b1;
                        else if (freq_q < 0) freq_q <= freq_q + 1'b1;
                    end else begin
                        leak_cnt_q <= leak_cnt_q + 1'b1;
                    end
                end
                // Safety net only: with a monotonic accumulator that advances
                // by less than one clock period per clock this cannot trigger,
                // but it keeps one word per UI if a threshold is ever skipped.
                if (!e_done_q && !edge_hit) begin
                    e_q      <= s;
                    d_out_q  <= d_q;
                    strobe_q <= 1'b1;
                end
                // The flags belong to the UI that has just started: it may
                // already have consumed its centre (nxt_c) when the wrap
                // landed past center_e.  nxt_e stays 0 because the edge
                // threshold is OS/2 periods from the UI start.
                c_done_q <= nxt_c;
                e_done_q <= nxt_e;
            end

            if (!lock_q && (edge_cnt_q >= LOCK_MAX))
                lock_q <= 1'b1;

            if (CLK_OUT_ENABLE)
                clk_hi_q <= wrap ? 1'b0 : ((center_hit) ? 1'b1 : clk_hi_q);
            else
                clk_hi_q <= 1'b0;
        end else begin
            strobe_q <= 1'b0;
            kick_q   <= '0;
        end
    end
endmodule
