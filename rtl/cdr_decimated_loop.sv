`timescale 1ns/1ps
`default_nettype none
// -----------------------------------------------------------------------------
// Decimated bang-bang loop filter for the quarter-rate parallel RX.
//
// Structure copied from cdr_bang_bang / cdr_multiphase: KI=1 incrementer,
// KP-shifted proportional kick, FMAX clamp on the integral word, vote-drought
// leak counter, LOCK_EDGES acquisition counter, LOCK_STALE_BITS stale counter.
// What changes is the update cadence and the meaning of the phase word.
//
// Decimation re-derivation
// ------------------------
//   * clk_q = 500 MHz, one cycle = 4 UI.  The BBPD delivers up to four votes
//     per cycle; the loop updates once every DECIM/4 cycles = once per DECIM
//     UI (DECIM default 16 -> 125 MHz effective update rate at 2 Gb/s).
//   * The per-cycle votes are summed over the window into vote_sum
//     (|vote_sum| <= DECIM); the update uses the window's net sign, i.e. one
//     bang-bang decision per DECIM UI instead of one per UI.  The effective
//     loop bandwidth is the single-sample bandwidth divided by DECIM, which
//     is the point of the parallel structure: fewer, cheaper updates at a
//     lower digital clock.
//   * freq_q is a DELAY correction in tap-fraction LSBs per window, not an
//     NCO rate: an early (positive) vote must DELAY the sampling phase, so
//     freq_q increases and r_q walks forward.  In cdr_bang_bang the same
//     early vote decreases an NCO rate increment, which delays the sampling
//     instant - the physical convention (early -> delay) is identical, the
//     state variable is not.  Do not "fix" the sign here; do not copy the
//     adpll_gen alexander_pd sign either (it is the opposite).
//   * KP re-derivation: a tap here is 0.5 UI (45 deg of the 500 MHz clock),
//     not 1/8 clock period as in cdr_multiphase.  The kick must stay well
//     inside the eye, so KP_SHIFT defaults to 5 -> kick = 2^(FREQ_FRAC-5)
//     frac LSBs = 1/32 tap = 1/64 UI per vote window.
//   * FMAX re-derivation: +-2^(FREQ_FRAC-1) frac LSBs per window = half a tap
//     per DECIM UI = +-31250 ppm at DECIM=16, far above any reference offset
//     the loop must track, and small enough that one slip fold-back always
//     clears the accumulator.
//   * Leak/stale counters count vote-less WINDOWS; the thresholds are scaled
//     down by log2(DECIM) so the per-UI rates match cdr_bang_bang:
//     2^LEAK_UI_BITS UIs without a transition still leak one LSB, and
//     LOCK_STALE_BITS UIs still assert lock_stale.
//   * LOCK_EDGES counts windows that carried a vote (lock budget in UI is
//     LOCK_EDGES * DECIM).
//
// Phase word and pi_code
// ----------------------
//   r_q = { tap[PH_W-1:0], frac[FREQ_FRAC-1:0] }
//   tap (pi_code) is the rotation of the 8-phase sample grid; one tap step
//   shifts all sampling instants by 0.5 UI (45 deg of the 500 MHz clock).
//   The fractional field gates when tap steps, exactly like cdr_multiphase's
//   ph_q.  In this ideal-clock v1 pi_code rotates the fixed 8 phases inside
//   cdr_parallel_top; in Stage B pi_code re-times the real multiphase_clock /
//   PI instead (walk the sampling phases, or interpolate between two adjacent
//   DCO phases - the port is agnostic, see docs/rx_parallel_spec.md).
//
// Slips / elastic events
// ----------------------
//   The raw windows march at the local clk_q rate while the data drifts at
//   the ppm offset; the sampler's content window therefore advances by
//   4 +- (drift + rotation steps) bits per group.  advance > 4 = the content
//   skipped bits (net sampling delay) -> the gearbox inserts fillers;
//   advance < 4 = the content revisited bits (net sampling advance) -> the
//   gearbox drops them; a mod-8 wrap of pi_code is a +-3-bit event.  The
//   loop reports one event per window (slip_evt + slip_pi + drop/insert
//   counts) and the gearbox repairs the stream at the tagged group.
//
//   Open item (v1, rotator mode): with fixed ideal clocks the Alexander
//   vote is determined by the dither-state parity for |o| inside the eye,
//   so the integral path has no net phase-error signal and the loop cannot
//   walk against the ppm drift - the eye budget (~+-0.125 UI) bounds the
//   tolerable drift to ~0.25 UI total.  True ppm tracking needs the Stage-B
//   clocking (CLK_FOLLOW=1: pi_code+pi_frac re-time the PI clock, the frac
//   field tracks the drift smoothly and clk_q follows); that path is
//   implemented here but its elastic-timing closure against the gearbox is
//   not verified yet - see docs/rx_parallel_spec.md.
// -----------------------------------------------------------------------------
module cdr_decimated_loop #(
    parameter int PH_W            = 3,   // taps per 4 UI; one tap = 0.5 UI
    parameter int FREQ_FRAC       = 8,   // fractional bits below one tap
    parameter int DECIM           = 16,  // UI per loop update (multiple of 4)
    parameter int KI              = 1,
    parameter int KP_SHIFT        = 5,   // kick = 2^(FREQ_FRAC-KP_SHIFT) per window
    parameter int LEAK_UI_BITS    = 12,  // per-UI drought before leaking 1 LSB
    parameter int LOCK_EDGES      = 16,  // vote-carrying windows to lock
    parameter int LOCK_STALE_BITS = 16,  // per-UI drought before lock_stale
    parameter int FRAC_BITS       = 12,  // freq_trim probe width
    // 1 = Stage-B clocking: pi_code+pi_frac re-time the sampler/PI clock
    // (clk_q follows), so every tap step is a +-1-UI content shift and the
    // fractional field tracks ppm smoothly.  0 = ideal fixed clocks with the
    // pi_code rotator in cdr_parallel_top: content shifts happen only at
    // even tap-boundary crossings (the +-1-tap dither inside a pair is
    // content-neutral).
    parameter bit CLK_FOLLOW      = 1'b1
) (
    input  wire                      clk_q,
    input  wire                      rst_n,
    input  wire                      enable,
    input  wire signed [2:0]         vote,        // -4..+4 from cdr_parallel_bbpd
    input  wire                      vote_valid,
    input  wire [PH_W-1:0]           phase_init,
    output wire [PH_W-1:0]           pi_code,     // rotation of the sample grid
    output wire [FREQ_FRAC-1:0]      pi_frac,     // fractional PI phase (tap LSBs/256)
    output wire                      lock,
    output wire                      lock_stale,
    output wire                      slip_evt,    // 1-cycle elastic event
    output wire [2:0]                slip_pi,     // rotation the event applies to
    output wire [1:0]                slip_drop,   // duplicated bits to drop (0-3)
    output wire [1:0]                slip_insert, // skipped bits to insert
    output wire signed [FRAC_BITS:0] freq_trim
);

    localparam int WIN_CYC   = (DECIM < 4) ? 1 : DECIM / 4;   // clk_q cycles/window
    localparam int WIN_W     = (WIN_CYC <= 1) ? 1 : $clog2(WIN_CYC);
    localparam int VS_W      = $clog2(DECIM + 2) + 1;         // |vote_sum| <= DECIM
    localparam int FW        = FREQ_FRAC + 4;                 // signed freq word
    localparam int RW        = PH_W + FREQ_FRAC;              // rotation word
    localparam logic signed [FW-1:0] KP   = FW'(1 << (FREQ_FRAC - KP_SHIFT));
    localparam logic signed [FW-1:0] KI_S = FW'(KI);
    // Integral clamp.  Rotator mode keeps the Stage-A +-2^(FREQ_FRAC-1)
    // (one full tap per window).  CLK_FOLLOW tightens it to
    // +-2^(FREQ_FRAC-4): the clamp bounds the post-acquisition overshoot
    // sweep of the re-timed sampling phase (a hot integral otherwise walks
    // the phase across the eye before the vote sign drains it), while still
    // giving a +-3900 ppm tracking range at DECIM=16 -- far above any
    // reference offset this loop must follow (verified to +-1000 ppm).
    localparam logic signed [FW-1:0] FMAX = FW'((CLK_FOLLOW) ? (1 << (FREQ_FRAC - 4))
                                                             : (1 << (FREQ_FRAC - 1)));
    localparam logic signed [FW-1:0] FMIN = -FMAX;
    localparam int LOCK_W  = (LOCK_EDGES <= 1) ? 1 : $clog2(LOCK_EDGES + 1);
    localparam bit LEAK_ON = (LEAK_UI_BITS >= 0);
    // Preserve the per-UI leak/stale rates across decimation.
    localparam int LEAK_DECIM  = (DECIM <= 1) ? 0 : $clog2(DECIM);
    localparam int LEAK_WBITS  = (LEAK_UI_BITS > LEAK_DECIM) ? (LEAK_UI_BITS - LEAK_DECIM) : 0;
    localparam int STALE_WBITS = (LOCK_STALE_BITS > LEAK_DECIM) ? (LOCK_STALE_BITS - LEAK_DECIM) : 0;
    localparam int LEAK_W  = (LEAK_WBITS < 1) ? 1 : LEAK_WBITS;
    localparam int STALE_W = (STALE_WBITS < 1) ? 1 : STALE_WBITS;
    localparam logic [LOCK_W-1:0]  LOCK_MAX  = LOCK_W'(LOCK_EDGES);
    localparam logic [LEAK_W-1:0]  LEAK_MAX  = {LEAK_W{1'b1}};
    localparam logic [STALE_W-1:0] STALE_MAX = {STALE_W{1'b1}};

    reg [WIN_W-1:0]        win_cnt_q;
    reg signed [VS_W-1:0]  vote_sum_q;
    reg                    win_vote_q;      // window carried >= 1 vote
    reg signed [FW-1:0]    freq_q;          // delay correction, taps/window
    reg signed [FW-1:0]    kick_q;          // one-window proportional kick
    reg [RW-1:0]           r_q;             // {frac, tap} rotation accumulator
    reg [PH_W-1:0]         tap_prev_q;      // mod-8 tap at the last window
    reg signed [3:0]       tap_dlt_u;
    reg                    slip_evt_q;
    reg [2:0]              slip_pi_q;
    reg [1:0]              slip_drop_q, slip_insert_q;
    reg [LOCK_W-1:0]       lock_cnt_q;
    reg [LEAK_W-1:0]       leak_cnt_q;
    reg [STALE_W-1:0]      stale_cnt_q;
    reg                    lock_q, stale_q;

    // Window accumulation, this cycle's vote included.
    wire signed [VS_W-1:0] vote_sum_c = vote_sum_q
        + ((vote_valid && enable) ? VS_W'(vote) : VS_W'(0));
    wire                   last_win  = (win_cnt_q == WIN_W'(WIN_CYC - 1));
    wire                   win_any   = win_vote_q || (vote_valid && enable);

    // Window end: one bang-bang decision on the window's net vote sign.
    wire signed [VS_W-1:0] s        = last_win ? vote_sum_c : vote_sum_q;
    wire                   vote_pos = (s > 0);
    wire                   vote_neg = (s < 0);

    reg signed [FW-1:0] freq_next;
    always_comb begin
        // KI == 1 -> incrementer, same trick as cdr_bang_bang.  Early
        // (positive window vote) must delay the sampling phase: freq_q
        // increases.
        if (KI == 1) begin
            freq_next = vote_pos ? (freq_q + FW'(1)) :
                        vote_neg ? (freq_q - FW'(1)) : freq_q;
        end else begin
            freq_next = freq_q + (vote_pos ? KI_S : vote_neg ? -KI_S : FW'(0));
        end
        if (freq_next > FMAX) freq_next = FMAX;
        else if (freq_next < FMIN) freq_next = FMIN;
    end

    wire signed [FW-1:0] kick_next  = vote_pos ? KP : vote_neg ? -KP : FW'(0);
    wire signed [FW-1:0] kick_apply = kick_q;   // applied one window late

    // Rotation word: persistent walk (freq_q, the integrator) plus the
    // proportional kick (the damping term - the kick moves the sampling
    // phase immediately, in clock-following mode by kick*0.98 ps, far below
    // one UI, so it never shifts the content).  Arithmetic is sign-extended
    // at FW width; the low RW bits are the mod-8-tap rotation and wraparound
    // is intended.
    wire signed [FW:0]  r_step = $signed({freq_q[FW-1], freq_q})
                               + $signed({kick_apply[FW-1], kick_apply});
    wire signed [FW:0]  r_sum  = $signed({{(FW-RW+1){r_q[RW-1]}}, r_q}) + r_step;
    wire [RW-1:0]       r_next = r_sum[RW-1:0];

    // Slip detection: the rotator remaps the fixed 8-phase samples by tap
    // with a cross-window borrow, so the sampled bit sequence stays
    // time-contiguous for +-1 tap steps, but every time the UNWRAPPED tap
    // walk crosses an even boundary the content window shifts one UI: one
    // bit is skipped (f up, net delay) or duplicated (f down, net advance).
    // f = tap >> 1 (arithmetic) changes exactly at those crossings; the
    // +-1-tap dither inside a pair never changes it.  The loop tracks the
    // unwrapped walk, compares f window to window, and reports each shift to
    // the gearbox (slip_skip / slip_dup) so the word stream stays
    // bit-continuous.  In Stage B the same shifts happen physically at the
    // clk_q divider of the re-timed PI clock; the ports stay as the
    // consistency check.

    assign pi_code    = r_q[RW-1:FREQ_FRAC];
    assign pi_frac    = r_q[FREQ_FRAC-1:0];
    assign lock       = lock_q;
    assign lock_stale = stale_q;
    assign slip_evt    = slip_evt_q;
    assign slip_pi     = slip_pi_q;
    assign slip_drop   = slip_drop_q;
    assign slip_insert = slip_insert_q;

    // Tap walk of this window's applied rotation (freq + kick), unwrapped
    // from the mod-8 tap field to -4..+4 taps; f = floor(0.375 + r_u/2) is
    // the content-window shift in UIs (arithmetic >> 1 on the integer walk).
    // CLK_FOLLOW: the content window advances when the SAMPLING PHASE
    // crosses a data bit boundary.  At the Alexander equilibrium the edge
    // taps park on the transitions (+skew), so that boundary crossing sits
    // ONE FULL TAP (2^FREQ_FRAC frac LSBs) away from the {tap,frac} word
    // boundary -- the content index is (r - 1 tap) >> 1, not r >> 1.  With
    // this offset the reported slip lands within a window of the physical
    // +-1-UI content shift (the residual error is the bang-bang dither,
    // +-KP LSBs) instead of up to half a tap-walk cycle late, and the
    // gearbox drop/insert repair tracks the discrete content events the
    // breathing recovered clock leaves.  Rotator mode (fixed clocks) keeps
    // the verified Stage-A arithmetic (shifts at the even tap boundaries).
    wire [RW-1:0] r_adj = r_next - RW'(1 << FREQ_FRAC);
    wire [PH_W-1:0] tap_next = CLK_FOLLOW ? r_adj[RW-1:FREQ_FRAC]
                                          : r_next[RW-1:FREQ_FRAC];
    // Content-window advance between consecutive groups.  In clock-following
    // mode the +-1-raw-tap steps (0.25 ns, half a UI) are content-neutral -
    // the data tap moves centre -> boundary and the +0.02 ns transition skew
    // keeps the boundary sample on the old bit - so only the COARSE steps
    // (tap[2:1], 0.5 ns = 1 UI) shift the content, by +-1 UI each.  f = pi>>1
    // is a mod-4 quantity (one full coarse turn = one word = identity), so
    // the content advance is 4 + (f_next - f_prev) mod 4, mapped to -1..+2:
    // advance > 4 = the content skipped bits -> the gearbox inserts fillers;
    // advance < 4 = the content revisited bits -> the gearbox drops them.
    // In rotator mode (fixed clocks) the same arithmetic holds for the
    // borrow mapping.
    // f = pi>>1 is the content-window index (mod 4): the content start is
    // 4*window + f, and the advance between groups is 4 + df (mod-4 f).
    // CLK_FOLLOW debounce: the walk jitters +-KP LSBs around the content
    // boundary (bang-bang dither, and vote droughts let the phase wander
    // freely for up to a PRBS run), so a single-window f change can be a
    // dither crossing that reverts next window.  Report only a change that
    // HELD for one window (f(n+1) == f(n) != f(n-1)); the repair then lands
    // one window (4 UI) late -- well inside the gearbox/mask tolerance --
    // and the jitter pairs (A->B->A) never report.
    reg  [PH_W-1:0] f_hold_q;                     // f(n-1)
    reg  [PH_W-1:0] f_hold2_q;                    // f(n-2)
    wire [PH_W-1:0] f_nextf = tap_next >> 1;      // f(n+1)
    wire [PH_W-1:0] f_nowf  = tap_prev_q >> 1;    // f(n)
    wire [PH_W-1:0] f_holdf = f_hold_q >> 1;      // f(n-1)
    wire [PH_W-1:0] f_hold2f= f_hold2_q >> 1;     // f(n-2)
    wire [PH_W-1:0] f_next  = CLK_FOLLOW ? f_nowf  : f_nextf;
    wire [PH_W-1:0] f_prev  = CLK_FOLLOW ? f_hold2f: f_nowf;
    // CLK_FOLLOW: the new content index must have held for TWO windows.
    wire        f_held      = CLK_FOLLOW ? (f_nextf == f_nowf) && (f_nowf == f_holdf)
                                         : 1'b1;
    wire [1:0]        dfm   = f_next[1:0] - f_prev[1:0];       // mod 4
    wire signed [2:0] adv_off = (dfm == 2'd3) ? -3'sd1 : $signed({1'b0, dfm});
    wire signed [3:0] adv   = 4'sd4 + adv_off;                 // 3..6
    wire       adv_skip = (adv > 4);
    wire       adv_dup  = (adv < 4);
    wire [1:0] skip_cnt = adv[1:0] - 2'd4;      // adv=5,6 -> 1,2
    wire [1:0] dup_cnt  = 2'd4 - adv[1:0];      // adv=3     -> 1

    assign freq_trim  = (FW+1)'($signed(freq_q));

    always_ff @(posedge clk_q or negedge rst_n) begin
        if (!rst_n) begin
            win_cnt_q   <= '0;
            vote_sum_q  <= '0;
            win_vote_q  <= 1'b0;
            freq_q      <= '0;
            kick_q      <= '0;
            r_q         <= {phase_init, FREQ_FRAC'(0)};
            tap_prev_q  <= phase_init;
            f_hold_q    <= phase_init;
            f_hold2_q   <= phase_init;
            slip_evt_q   <= 1'b0;
            lock_cnt_q  <= '0;
            leak_cnt_q  <= '0;
            stale_cnt_q <= '0;
            lock_q      <= 1'b0;
            stale_q     <= 1'b0;
        end else if (enable) begin
            vote_sum_q <= vote_sum_c;
            if (vote_valid)
                win_vote_q <= 1'b1;
            slip_evt_q   <= 1'b0;

            if (!last_win) begin
                win_cnt_q <= win_cnt_q + 1'b1;
            end else begin
                // Window boundary: the decimated update happens once here.
                win_cnt_q  <= '0;
                vote_sum_q <= '0;
                win_vote_q <= 1'b0;
                kick_q     <= '0;             // kick cleared after one window

                // Tap walk and slip reporting run every window: the ppm
                // drift and the mod-8 wrap do not wait for a vote.  The
                // content advance per group is 4 + (f(pi_next)-f(pi_prev))
                // with f = tap>>1: in clock-following mode f counts half-UI
                // coarse steps (a +-1-raw-tap dither is content-neutral, a
                // +-2 step shifts the content +-1 UI); in rotator mode the
                // same arithmetic holds for the fixed-clock borrow mapping.
                tap_prev_q <= tap_next;
                f_hold_q   <= tap_prev_q;
                f_hold2_q  <= f_hold_q;
`ifdef TB_DEBUG_SLIP
                $display("SLIPDBG t=%0t tapn=%0d tapp=%0d adv=%0d sk=%b du=%b win=%b",
                         $time, tap_next, tap_prev_q, adv, adv_skip, adv_dup, win_any);
`endif
                if ((adv_skip || adv_dup) && f_held) begin
                    slip_evt_q   <= 1'b1;
                    slip_pi_q    <= tap_next;
                    slip_drop_q   <= adv_dup  ? dup_cnt  : 2'b00;
                    slip_insert_q <= adv_skip ? skip_cnt : 2'b00;
                end

                if (win_any) begin
                    freq_q     <= freq_next;
                    kick_q     <= kick_next;  // kicks only on vote windows
                    r_q        <= r_next;
                    if (lock_cnt_q != LOCK_MAX)
                        lock_cnt_q <= lock_cnt_q + 1'b1;
                    if (!lock_q && (lock_cnt_q >= LOCK_MAX))
                        lock_q <= 1'b1;
                end else begin
                    // Vote-less window: drought counters.  The thresholds are
                    // in windows, scaled so the per-UI rate matches
                    // cdr_bang_bang (LEAK_WBITS / STALE_WBITS).
                    if (STALE_WBITS == 0) begin
                        stale_cnt_q <= '0;
                        stale_q     <= 1'b1;
                    end else if (stale_cnt_q == STALE_MAX) begin
                        stale_cnt_q <= '0;
                        stale_q     <= 1'b1;
                    end else begin
                        stale_cnt_q <= stale_cnt_q + 1'b1;
                    end
                    if (LEAK_ON) begin
                        if (LEAK_WBITS == 0 || leak_cnt_q == LEAK_MAX) begin
                            leak_cnt_q <= '0;
                            if (freq_q > 0)      freq_q <= freq_q - FW'(1);
                            else if (freq_q < 0) freq_q <= freq_q + FW'(1);
                        end else begin
                            leak_cnt_q <= leak_cnt_q + 1'b1;
                        end
                    end
                end
            end
        end
    end

endmodule
