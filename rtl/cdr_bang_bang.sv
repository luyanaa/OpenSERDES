`timescale 1ns/1ps
// 4x bang-bang CDR: Alexander BBPD + 2nd-order digital loop filter +
// fractional NCO.  The recovered bit rate is set by the NCO, not by a fixed
// divide-by-4, so it can track ppm continuously.
//
// Loop (per data sample):
//   v[n]   = +1 (early) / -1 (late) / 0 (no transition)
//   f[n+1] = sat(f[n] + KI*v[n])          // frequency integral
//   phi[n] = phi[n] + (1<<FR) + f[n]      // NCO, wraps once per UI
//   phi   += KP*v[n] at the data sample   // proportional phase kick
// A UI is 4 clock periods nominally and 3/5 when the NCO is off-nominal, so
// the average recovered rate follows the TX.  Data is captured when the NCO
// crosses the eye centre; the edge sample when it wraps.  Exactly one strobe
// is emitted per UI, so a 3-cycle UI cannot skip the centre and a 5-cycle UI
// cannot duplicate a bit.
module cdr_bang_bang #(
    parameter int OVERSAMPLE      = 4,
    parameter int SYNC_STAGES     = 3,
    parameter int NCO_FRAC        = 20,
    parameter int KI              = 1,
    parameter int KP_SHIFT        = 5,
    parameter int LOCK_EDGES      = 16,
    parameter int LOCK_STALE_BITS = 256,
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
    output logic signed [FRAC_BITS:0] freq_trim
);
    localparam int OS      = (OVERSAMPLE < 4) ? 4 : OVERSAMPLE;
    localparam int PH_W    = $clog2(OS);
    localparam int HALF    = OS / 2;
    localparam int N       = (SYNC_STAGES < 3) ? 3 : SYNC_STAGES;
    localparam int FR      = NCO_FRAC;
    localparam int PW      = FR + PH_W + 3;
    localparam int FW      = FR + 4;
    localparam logic [PW-1:0] ONE    = PW'(OS << FR);
    localparam logic [PW-1:0] CENTER = PW'((OS << FR) >>> 1);
    localparam logic [PW-1:0] CLKINC = PW'(1 << FR);
    // cdr_sync delays the sample by N clocks, so rotate the NCO phase by
    // N mod OS to keep the eye-centre crossing aligned with the delayed
    // sample stream.
    localparam logic [PW-1:0] PH0 = PW'(((OS - (N % OS)) % OS) * (1 << FR));
    localparam logic signed [FW-1:0] KP = FW'(1 << (FR - KP_SHIFT));
    localparam logic signed [FW-1:0] KI_S = FW'(KI);
    localparam logic signed [FW-1:0] FMAX = FW'(1 << (FR - 2));
    localparam logic signed [FW-1:0] FMIN = -FW'(1 << (FR - 2));
    localparam int LOCK_W  = (LOCK_EDGES <= 1) ? 1 : $clog2(LOCK_EDGES + 1);
    localparam int STALE_W = (LOCK_STALE_BITS <= 1) ? 1 : $clog2(LOCK_STALE_BITS + 1);
    localparam logic [LOCK_W-1:0]  LOCK_MAX  = LOCK_W'(LOCK_EDGES);
    localparam logic [STALE_W-1:0] STALE_MAX = STALE_W'(LOCK_STALE_BITS);

    logic [N-1:0]             samples;
    logic                     s;
    logic [PW-1:0]            phase_q;
    logic signed [FW-1:0]     freq_q;
    logic signed [FW-1:0]     inc_v;
    logic signed [FW-1:0]     freq_next;
    logic [PW-1:0]            phase_next;
    logic [PW-1:0]            phase_w;
    logic [PW-1:0]            phase_adj;
    logic signed [PW:0]       phase_s;
    logic signed [FW-1:0]     kp_kick;
    logic signed [FW-1:0]     ki_kick;
    logic [PW-1:0]            center_e;
    logic [PW-1:0]            edge_e;
    logic [PW-1:0]            off_e;
    logic                     edge_hit;
    logic                     wrap;
    logic                     center_hit;
    logic                     trans;
    logic signed [1:0]        vote;
    logic                     d_q;
    logic                     d_prev_q;
    logic                     e_q;
    logic                     d_out_q;
    logic                     strobe_q;
    logic                     clk_hi_q;
    logic [LOCK_W-1:0]        edge_cnt_q;
    logic [STALE_W-1:0]       stale_cnt_q;
    logic                     lock_q;
    logic                     stale_q;

    cdr_sync #(.STAGES(N)) u_sync (
        .clk(clk), .rst_n(rst_n), .async_in(data_in), .samples(samples));

    assign s = samples[N-1];

    // NCO increment = one clock period plus the frequency correction.  Clamp
    // the correction so the increment stays positive.
    always_comb begin
        inc_v = (FW'(CLKINC) + freq_q);
        if (inc_v < FW'(CLKINC >>> 2))
            inc_v = FW'(CLKINC >>> 2);
        phase_next = phase_q + PW'(inc_v);
        wrap       = (phase_next >= ONE);
        phase_w    = wrap ? (phase_next - ONE) : phase_next;
        // Eye-centre crossing, shifted by the external phase request.
        // The sync sample is N clocks old, so both crossings are shifted by
        // N mod OS clock periods; the strobe fires at the delayed UI boundary.
        off_e      = (PW'((N % OS) * (1 << FR)) + PW'(phase_offset) * CLKINC) & (ONE - 1'b1);
        center_e   = (off_e + CENTER) & (ONE - 1'b1);
        edge_e     = off_e;
        center_hit = (phase_q < center_e) && (phase_next >= center_e) && !wrap;
        edge_hit   = (phase_q < edge_e)   && (phase_next >= edge_e)   && !wrap;
        // Wrap-safe proportional phase kick: subtracting KP can underflow.
        phase_s = $signed({1'b0, phase_w}) - kp_kick;
        if (phase_s < 0)
            phase_s = phase_s + $signed({1'b0, ONE});
        else if (phase_s >= $signed({1'b0, ONE}))
            phase_s = phase_s - $signed({1'b0, ONE});
        phase_adj = phase_s[PW-1:0];
    end

    assign trans    = center_hit && (s != d_prev_q);
    assign data_out = d_out_q;
    assign sample_strobe = strobe_q;
    assign clk_out  = CLK_OUT_ENABLE ? clk_hi_q : 1'b0;
    assign lock     = lock_q;
    assign lock_stale = stale_q;
    assign freq_trim  = freq_q[FRAC_BITS:0];

    // Alexander PD: with a transition, e == d_prev means the edge sample is
    // still the old bit, i.e. the clock is early (+1).
    always_comb begin
        vote = 2'sd0;
        if (trans) vote = (e_q == d_prev_q) ? 2'sd1 : -2'sd1;
        ki_kick   = KI_S * vote;
        kp_kick   = KP * vote;
        freq_next = freq_q - ki_kick;
        if (freq_next > FMAX) freq_next = FMAX;
        else if (freq_next < FMIN) freq_next = FMIN;
    end

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            phase_q <= '0; freq_q <= '0; d_q <= 1'b0; d_prev_q <= 1'b0;
            e_q <= 1'b0; d_out_q <= 1'b0; strobe_q <= 1'b0; clk_hi_q <= 1'b0;
            edge_cnt_q <= '0; stale_cnt_q <= '0; lock_q <= 1'b0; stale_q <= 1'b0;
        end else if (flush) begin
            phase_q <= '0; freq_q <= '0; d_q <= 1'b0; d_prev_q <= 1'b0;
            e_q <= 1'b0; d_out_q <= 1'b0; strobe_q <= 1'b0; clk_hi_q <= 1'b0;
            edge_cnt_q <= '0; stale_cnt_q <= '0; lock_q <= 1'b0; stale_q <= 1'b0;
        end else if (enable) begin
            // NCO update (with proportional kick on a data sample).
            if (center_hit)
                phase_q <= phase_adj;
            else
                phase_q <= phase_w;

            strobe_q <= 1'b0;
            if (center_hit) begin
                d_q      <= s;
                d_prev_q <= s;
                // Leaky integral: removes the PD bias at zero ppm, while a
                // persistent ppm bias still drives the state away from zero.
                if (trans)
                    freq_q <= freq_next;
                else if (freq_q > 0)
                    freq_q <= freq_q - 1'b1;
                else if (freq_q < 0)
                    freq_q <= freq_q + 1'b1;
                if (trans) begin
                    if (edge_cnt_q != LOCK_MAX)
                        edge_cnt_q <= edge_cnt_q + 1'b1;
                    stale_cnt_q <= '0;
                    stale_q     <= 1'b0;
                end
            end
            if (edge_hit) begin
                e_q      <= s;
                d_out_q  <= d_q;
                strobe_q <= 1'b1;
                if (!trans) begin
                    if (stale_cnt_q != STALE_MAX)
                        stale_cnt_q <= stale_cnt_q + 1'b1;
                    if ((stale_cnt_q + 1'b1) >= STALE_MAX)
                        stale_q <= 1'b1;
                end
            end

            if (!lock_q && (edge_cnt_q >= LOCK_MAX))
                lock_q <= 1'b1;

            if (CLK_OUT_ENABLE)
                clk_hi_q <= wrap ? 1'b0 : ((center_hit) ? 1'b1 : clk_hi_q);
            else
                clk_hi_q <= 1'b0;
        end else begin
            strobe_q <= 1'b0;
        end
    end
endmodule
