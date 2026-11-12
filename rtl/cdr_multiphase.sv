`timescale 1ns/1ps
// 4x bang-bang CDR with a DLL phase-select backend.
//
// Why this exists
// ---------------
// cdr_bang_bang keeps the sampling instant and the loop arithmetic in the same
// 100 MHz cycle: phase_q (18 bit) + inc_r -> wrap/threshold compare -> the
// register that decides when the next sample is taken.  C2 moved the control
// word behind a register and got the routed depth to 12.04 ns, but the NCO still
// has to resolve one clock period (10 ns) to 2^-16, and the accumulator width is
// what makes the path long.
//
// A DLL removes the fine resolution from the digital arithmetic.  It hands the
// design 2^PH_W equally spaced phases of the sample clock, so the digital loop
// only has to choose a phase, not generate one.  The phase accumulator shrinks
// from NCO_FRAC=16 fractional bits to PH_W=3 integer tap bits plus a slow
// fractional term for ppm tracking, and the sampling instant is produced by the
// analog phase mux instead of by the accumulator carry chain.
//
// Phase representation
// --------------------
// One UI is OVERSAMPLE sample slots.  Each slot has 2^PH_W taps:
//
//   ph_q = { slot[SLOT_W-1:0], tap[PH_W-1:0], frac[FREQ_FRAC-1:0] }
//
// and one sample clock advances it by exactly 2^PH_W tap units, i.e. one slot.
// The tap field is therefore constant for a whole UI and only changes when the
// frequency term is applied at the UI boundary.  That is what makes the tap a
// plain register slice: no adder or comparator sits between the loop state and
// the sampler select.
//
// The loop filter is the same Alexander BBPD + 2nd-order filter as
// cdr_bang_bang, with the same sign convention: an early vote (the edge sample
// still carries the old bit) slows the loop down.
//
// Sampling interface
// ------------------
// `smp` is the data already sampled at phase `tap` by the DLL/sampler cell.
// The phase mux is set up from `tap` before the selected edge, and the sample
// is read on the following clk.  The core therefore contains no analog timing
// path; the only sampler requirement is that the selected edge has enough setup
// before the next clk edge (see docs/dll_backend.md).
//
// This module is the digital half of the backend.  It synthesises and times as
// ordinary RTL; the DLL and the phase mux are the analog macro.
module cdr_multiphase #(
    parameter int OVERSAMPLE    = 4,
    // DLL taps per sample-clock period.  2^PH_W must divide evenly into the
    // phase grid; 3 bits = 1/32 UI resolution at OS=4 (1.25 ns at 100 MHz).
    parameter int PH_W          = 3,
    // Fractional bits below one tap LSB.  8 bits = 1/256 tap = 122 ppm per LSB
    // at OS=4, close to the 15 ppm the 16-bit NCO had and enough to hold
    // +-1000 ppm; the whole phase register is then 13 bits instead of 18, and
    // no wide compare sits on the sampling path.
    parameter int FREQ_FRAC     = 8,

    parameter int KI            = 1,
    // Kick is 2^(FREQ_FRAC-KP_SHIFT) tap LSB.  Default 2 -> 0.25 tap LSB
    // (312 ps at 100 MHz), the same proportional step cdr_bang_bang uses.
    parameter int KP_SHIFT      = 2,
    parameter int LEAK_UI_BITS  = 12,
    parameter int LOCK_EDGES    = 16,
    parameter int LOCK_STALE_BITS = 16,
    parameter int PHASE_CTRL_W  = 2,
    parameter int FRAC_BITS     = 12,
    parameter bit CLK_OUT_ENABLE = 1'b1
) (
    input  logic                      clk,
    input  logic                      rst_n,
    input  logic                      enable,
    input  logic                      flush,
    input  logic                      smp,
    input  logic [PHASE_CTRL_W-1:0]   phase_offset,
    output logic [PH_W-1:0]           tap,
    output logic                      data_out,
    output logic                      sample_strobe,
    output logic                      clk_out,
    output logic                      lock,
    output logic                      lock_stale,
    output logic [FRAC_BITS:0]        freq_trim
);
    localparam int OS      = (OVERSAMPLE < 4) ? 4 : OVERSAMPLE;
    localparam int SLOT_W  = $clog2(OS);
    localparam int PW      = PH_W + SLOT_W;
    localparam int W       = PW + FREQ_FRAC;
    localparam int FW      = FREQ_FRAC + 4;             // signed
    localparam logic [W-1:0]  STEP = W'(1 << (FREQ_FRAC + PH_W));
    localparam logic signed [FW-1:0] KP    = FW'(1 << (FREQ_FRAC - KP_SHIFT));
    localparam logic signed [FW-1:0] KI_S  = FW'(KI);
    // Same +-25% of a UI per UI bound the NCO used; anything larger cannot be a
    // frequency offset and would only make the phase accumulator run away.
    localparam logic signed [FW-1:0] FMAX  = FW'(1 << (FREQ_FRAC + 2));
    localparam logic signed [FW-1:0] FMIN  = -FW'(1 << (FREQ_FRAC + 2));
    localparam int LOCK_W  = (LOCK_EDGES <= 1) ? 1 : $clog2(LOCK_EDGES + 1);
    localparam int STALE_W = (LOCK_STALE_BITS <= 1) ? 1 : $clog2(LOCK_STALE_BITS + 1);
    localparam bit LEAK_ON       = (LEAK_UI_BITS >= 0);
    localparam bit LEAK_EVERY_UI = (LEAK_UI_BITS == 0);
    localparam int LEAK_W  = (LEAK_UI_BITS < 1) ? 1 : LEAK_UI_BITS;
    localparam logic [LOCK_W-1:0]  LOCK_MAX  = LOCK_W'(LOCK_EDGES);
    localparam logic [STALE_W-1:0] STALE_MAX = STALE_W'(LOCK_STALE_BITS);
    localparam logic [LEAK_W-1:0]  LEAK_MAX  = {LEAK_W{1'b1}};

    logic [W-1:0]             ph_q;
    logic [PW-1:0]            ph_ui;
    logic [SLOT_W-1:0]        slot_q;
    logic [PH_W-1:0]          tap_q;
    logic [PW-1:0]            c_e, e_e;
    logic [PW-1:0]            ph_next_ui;
    logic                     ui_wrap, cx_c, cx_e, nxt_c, nxt_e;
    logic                     centre_x, edge_x;
    logic                     centre_hit, edge_hit;
    logic                     at_ui;
    logic signed [FW-1:0]     freq_q;
    logic signed [FW-1:0]     kick_q;
    logic signed [FW-1:0]     freq_next;
    logic [W-1:0]             ph_step;
    logic [W-1:0]             step_ui_q;
    logic [W-1:0]             ph_next;
    logic signed [1:0]        vote;
    logic                     trans;
    logic                     d_q, d_prev_q, e_q, d_out_q, strobe_q, clk_hi_q;
    logic [LOCK_W-1:0]        edge_cnt_q;
    logic [STALE_W-1:0]       stale_cnt_q;
    logic [LEAK_W-1:0]        leak_cnt_q;
    logic                     trans_ui_q;
    logic                     lock_q, stale_q;

    // Tap and slot are register slices.  This is the entire sampling control
    // path: ph_q -> tap -> (phase mux, outside this module).
    assign ph_ui  = ph_q[FREQ_FRAC +: PW];
    assign slot_q = ph_ui[PH_W +: SLOT_W];
    assign tap_q  = ph_ui[0 +: PH_W];
    assign tap    = tap_q;

    // Event detection is a threshold crossing on the UI phase, exactly like
    // cdr_bang_bang.  A slot-equality compare would only be correct if the
    // accumulator started on the data eye; a crossing gives the loop the full
    // one-UI capture range (a slot compare can false-lock at the bit edge and
    // never walk out, because the tap alone spans only one slot).
    assign c_e = PW'(phase_offset << PH_W);
    assign e_e = c_e + PW'((OS / 2) << PH_W);

    always_comb begin
        ph_next_ui = ph_next[FREQ_FRAC +: PW];
        ui_wrap    = (ph_next_ui < ph_ui);
        if (ui_wrap) begin
            cx_c = ph_ui < c_e;
            nxt_c = ph_next_ui >= c_e;
            cx_e = ph_ui < e_e;
            nxt_e = ph_next_ui >= e_e;
        end else begin
            cx_c = (ph_ui < c_e) && (ph_next_ui >= c_e);
            cx_e = (ph_ui < e_e) && (ph_next_ui >= e_e);
            nxt_c = 1'b0;
            nxt_e = 1'b0;
        end
        // Sampler hand-off: the phase mux samples data on the selected edge
        // and this core reads it on the next clk.  The event pulse is
        // registered once so it lands in the same cycle as the sample it
        // classifies, without putting the phase clock on a data path.
        centre_x = cx_c || nxt_c;
        edge_x   = cx_e || nxt_e;
    end

    assign at_ui = (slot_q == SLOT_W'(OS - 1));

    // The frequency term is applied once per UI, at the last slot, so the tap
    // is stable for all OVERSAMPLE samples of a UI.  freq_q and kick_q are
    // signed; sign-extend into the phase width.  Same sign as cdr_bang_bang:
    // an early vote lowers the increment (and kicks the phase back) so the
    // sampling grid waits for the data.
    // The fine correction is registered one UI ahead of the boundary it applies
    // to.  Forming STEP + freq - kick in the same cycle as the phase add made
    // freq_q -> add -> compare -> centre_hit the critical path (8.79 ns
    // pre-PnR).  The correction now settles in step_ui_q, and the phase add is
    // a single registered-operand add.  The loop already applies a vote one UI
    // later; this makes it two, which is 40 ns at the 25 Mb/s target and far
    // inside the loop's time constant.
    always_comb begin
        ph_step = at_ui ? step_ui_q : STEP;
        ph_next = ph_q + ph_step;
    end

    // Alexander PD, same convention as cdr_bang_bang.
    assign trans = centre_hit && (smp != d_prev_q);
    always_comb begin
        vote = 2'sd0;
        if (trans) vote = (e_q == d_prev_q) ? 2'sd1 : -2'sd1;
    end

    always_comb begin
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
    // Observation port: sign-extend the frequency word into the fixed-width
    // probe (FW is only FREQ_FRAC+4 bits, which can be narrower than
    // FRAC_BITS+1 when the fine word is small).
    assign freq_trim     = (FRAC_BITS + 1)'($signed(freq_q));

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            ph_q <= '0; freq_q <= '0; kick_q <= '0; step_ui_q <= STEP;
            centre_hit <= 1'b0; edge_hit <= 1'b0;
            d_q <= 1'b0; d_prev_q <= 1'b0; e_q <= 1'b0; d_out_q <= 1'b0;
            strobe_q <= 1'b0; clk_hi_q <= 1'b0;
            leak_cnt_q <= '0; trans_ui_q <= 1'b0;
            edge_cnt_q <= '0; stale_cnt_q <= '0; lock_q <= 1'b0; stale_q <= 1'b0;
        end else if (flush) begin
            ph_q <= '0; freq_q <= '0; kick_q <= '0; step_ui_q <= STEP;
            centre_hit <= 1'b0; edge_hit <= 1'b0;
            d_q <= 1'b0; d_prev_q <= 1'b0; e_q <= 1'b0; d_out_q <= 1'b0;
            strobe_q <= 1'b0; clk_hi_q <= 1'b0;
            leak_cnt_q <= '0; trans_ui_q <= 1'b0;
            edge_cnt_q <= '0; stale_cnt_q <= '0; lock_q <= 1'b0; stale_q <= 1'b0;
        end else if (enable) begin
            ph_q <= ph_next;
            // Capture the step that the *next* UI boundary will use.  It is
            // formed from the registers, not from this cycle's vote, so the
            // sampler-to-PD path never feeds the phase adder.
            if (at_ui)
                step_ui_q <= STEP + W'($signed(freq_q) - $signed(kick_q));
            centre_hit <= centre_x;
            edge_hit   <= edge_x;
            strobe_q <= 1'b0;

            if (centre_hit) begin
                d_q      <= smp;
                d_prev_q <= smp;
                if (trans) begin
                    freq_q     <= freq_next;
                    kick_q     <= KP * vote;
                    trans_ui_q <= 1'b1;
                    stale_cnt_q <= '0;
                    stale_q     <= 1'b0;
                    if (edge_cnt_q != LOCK_MAX)
                        edge_cnt_q <= edge_cnt_q + 1'b1;
                end
            end

            if (edge_hit) begin
                e_q      <= smp;
                d_out_q  <= d_q;
                strobe_q <= 1'b1;
                if (!trans) begin
                    if (stale_cnt_q != STALE_MAX)
                        stale_cnt_q <= stale_cnt_q + 1'b1;
                    if ((stale_cnt_q + 1'b1) >= STALE_MAX)
                        stale_q <= 1'b1;
                end
            end

            // Recovered bit clock monitor: one UI is OS sample clocks, so toggle
            // at the two slot-0 boundaries.  The real recovered clock is the
            // selected DLL phase in the analog macro; this is the divided
            // version used for the digital endpoints.
            if (slot_q == '0)
                clk_hi_q <= 1'b1;
            else if (slot_q == SLOT_W'(OS / 2))
                clk_hi_q <= 1'b0;

            if (at_ui) begin
                // Do not clear a kick that was just issued in this same cycle:
                // a centre at the last slot votes and crosses the UI boundary
                // at the same edge (phase_offset = OS-1).
                if (!(centre_hit && trans)) kick_q <= '0;
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
                if (!lock_q && edge_cnt_q >= LOCK_MAX)
                    lock_q <= 1'b1;
            end
        end
    end
endmodule
