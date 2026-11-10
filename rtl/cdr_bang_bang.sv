`timescale 1ns/1ps
// 4x oversampling bang-bang CDR prototype (continuous-phase vote loop).
//
// STATUS 2026-11-10: PROTOTYPE.  Bit-exact at 0 ppm and +100 ppm over
// 5000 bits; it does NOT yet track +/-300 or +/-700 ppm (the vote stepper
// walks the selector to a boundary).  Not wired into serdes_rx_digital,
// the generator, make sim or regress.  Do not use for a link claim.
// 2 samples/UI is fundamentally ambiguous (edge sample on the boundary),
// which is why this design is 4x, not 2x.
//
// Sampling: cdr_sync (delay a multiple of OS) -> 4 phases per UI.  sel_q is the
// DATA phase, the EDGE phase is sel_q + OS/2 (the bit boundary when centred).
// A data instant compares d_prev / edge / d (Alexander).  The vote drives a
// hysteresis phase stepper: |vote_acc| >= STEP_VOTES moves sel by one sample
// (1/OS UI).  That is the phase (proportional) path.  The same stepper keeps
// stepping while the boundary drifts, which tracks frequency.  The output
// word cadence is fixed at one strobe per UI, so a phase step never slips a
// bit.  Acquisition uses a direct boundary measurement (max per-phase change
// count) to initialise sel.
module cdr_bang_bang #(
    parameter int OVERSAMPLE      = 4,
    parameter int SYNC_STAGES     = 3,
    parameter int ACQ_WINDOW      = 64,
    parameter int LOCK_WINDOWS    = 2,
    parameter int LOCK_STALE_BITS = 256,
    parameter int STEP_VOTES      = 6,
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
    localparam int N_BASE  = (SYNC_STAGES < 3) ? 3 : SYNC_STAGES;
    localparam int N       = ((N_BASE + OS - 1) / OS) * OS;
    localparam int CNT_W   = 8;
    localparam int VOTE_W  = 6;
    localparam int WIN_W   = (ACQ_WINDOW <= 1) ? 1 : $clog2(ACQ_WINDOW + 1);
    localparam int LWIN_W  = (LOCK_WINDOWS <= 1) ? 1 : $clog2(LOCK_WINDOWS + 1);
    localparam int STALE_W = (LOCK_STALE_BITS <= 1) ? 1 : $clog2(LOCK_STALE_BITS + 1);

    localparam logic [PH_W-1:0] HALF_C  = PH_W'(HALF);
    localparam logic [PH_W-1:0] OS_M1   = PH_W'(OS-1);
    localparam logic [WIN_W-1:0] WIN_M1 = WIN_W'(ACQ_WINDOW - 1);
    localparam logic [LWIN_W-1:0] LWIN_C = LWIN_W'(LOCK_WINDOWS);
    localparam logic [STALE_W-1:0] STALE_MAX = STALE_W'(LOCK_STALE_BITS);
    localparam logic signed [VOTE_W-1:0] STEP_V = VOTE_W'(STEP_VOTES);

    logic [N-1:0]             samples;
    logic [PH_W-1:0]          ph_q;
    logic [PH_W-1:0]          sel_q;
    logic [PH_W-1:0]          edge_ph;
    logic                     d_prev_q;
    logic                     e_q;
    logic                     d_q;
    logic                     d_out_q;
    logic                     d_next;
    logic                     strobe_q;
    logic                     clk_hi_q;
    logic signed [VOTE_W-1:0] vote_q;
    logic                     vote_up;
    logic                     vote_dn;
    logic [OS-1:0][CNT_W-1:0] cnt_q;
    logic [CNT_W-1:0]         max_v;
    logic [PH_W-1:0]          max_i;
    logic [WIN_W-1:0]         win_q;
    logic [LWIN_W-1:0]        lock_win_q;
    logic [STALE_W-1:0]       stale_cnt_q;
    logic                     lock_q;
    logic                     stale_q;
    logic                     s;
    logic                     s_d1;
    logic                     diff;
    logic                     trans;
    logic                     data_now;
    logic                     edge_now;
    logic                     ui_end;

    cdr_sync #(.STAGES(N)) u_sync (
        .clk(clk), .rst_n(rst_n), .async_in(data_in), .samples(samples));

    assign s       = samples[N-1];
    assign diff    = s ^ s_d1;
    assign ui_end  = (ph_q == OS_M1);
    assign edge_ph = sel_q ^ HALF_C;
    assign data_now = (ph_q == sel_q);
    assign edge_now = (ph_q == edge_ph);
    assign trans   = data_now && (s != d_prev_q);
    assign vote_up = trans && (e_q == d_prev_q);
    assign vote_dn = trans && (e_q != d_prev_q);
    assign d_next  = data_now ? s : d_q;

    assign data_out      = d_out_q;
    assign sample_strobe = strobe_q;
    assign clk_out       = CLK_OUT_ENABLE ? clk_hi_q : 1'b0;
    assign lock          = lock_q;
    assign lock_stale    = stale_q;
    assign freq_trim     = $signed({{(FRAC_BITS+1-PH_W){1'b0}}, sel_q})
                         - $signed({{(FRAC_BITS+1-PH_W){1'b0}}, HALF_C});

    always_comb begin
        max_v = '0;
        max_i = '0;
        for (int i = 0; i < OS; i++) begin
            if (cnt_q[i] > max_v) begin
                max_v = cnt_q[i];
                max_i = i[PH_W-1:0];
            end
        end
    end

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            ph_q <= '0; sel_q <= HALF_C; d_prev_q <= 1'b0; e_q <= 1'b0;
            d_q <= 1'b0; d_out_q <= 1'b0; strobe_q <= '0; clk_hi_q <= 1'b0;
            vote_q <= '0; cnt_q <= '0; win_q <= '0; lock_win_q <= '0;
            stale_cnt_q <= '0; lock_q <= 1'b0; stale_q <= 1'b0; s_d1 <= 1'b0;
        end else if (flush) begin
            ph_q <= '0; sel_q <= HALF_C; d_prev_q <= 1'b0; e_q <= 1'b0;
            d_q <= 1'b0; d_out_q <= 1'b0; strobe_q <= '0; clk_hi_q <= 1'b0;
            vote_q <= '0; cnt_q <= '0; win_q <= '0; lock_win_q <= '0;
            stale_cnt_q <= '0; lock_q <= 1'b0; stale_q <= 1'b0; s_d1 <= 1'b0;
        end else if (enable) begin
            s_d1 <= s;
            d_q  <= d_next;
            if (ui_end) begin
                d_out_q  <= d_next;
                strobe_q <= 1'b1;
            end else begin
                strobe_q <= 1'b0;
            end
            if (edge_now)
                e_q <= s;
            if (data_now)
                d_prev_q <= s;

            // Alexander vote with leak toward zero.
            if (trans) begin
                if (vote_q != {VOTE_W{1'b1}})
                    vote_q <= vote_q + (vote_up ? 1'b1 : -1'b1);
            end else if (data_now) begin
                if (vote_q > 0)      vote_q <= vote_q - 1'b1;
                else if (vote_q < 0) vote_q <= vote_q + 1'b1;
            end

            // Boundary measurement for acquisition.
            if (diff) begin
                if (cnt_q[ph_q] != {CNT_W{1'b1}})
                    cnt_q[ph_q] <= cnt_q[ph_q] + 1'b1;
                stale_cnt_q <= '0;
                stale_q     <= 1'b0;
            end else if (ui_end) begin
                if (stale_cnt_q != STALE_MAX)
                    stale_cnt_q <= stale_cnt_q + 1'b1;
                if ((stale_cnt_q + 1'b1) >= STALE_MAX)
                    stale_q <= 1'b1;
            end

            if (ui_end) begin
                ph_q <= '0;
                if (win_q == WIN_M1) begin
                    win_q <= '0;
                    if (max_v != '0) begin
                        if (!lock_q)
                            sel_q <= max_i + HALF_C + phase_offset[PH_W-1:0];
                        if (lock_win_q != LWIN_C)
                            lock_win_q <= lock_win_q + 1'b1;
                    end
                    cnt_q <= '0;
                end else begin
                    win_q <= win_q + 1'b1;
                end
            end else begin
                ph_q <= ph_q + 1'b1;
            end

            // Continuous phase stepper (after lock).
            if (ui_end && lock_q && (vote_q >= STEP_V)) begin
                sel_q  <= sel_q - 1'b1;
                vote_q <= '0;
            end else if (ui_end && lock_q && (vote_q <= -STEP_V)) begin
                sel_q  <= sel_q + 1'b1;
                vote_q <= '0;
            end

            if (!lock_q && (lock_win_q >= LWIN_C))
                lock_q <= 1'b1;

            if (CLK_OUT_ENABLE)
                clk_hi_q <= (ph_q == HALF_C) ? 1'b1 : (ui_end ? 1'b0 : clk_hi_q);
            else
                clk_hi_q <= 1'b0;
        end else begin
            strobe_q <= 1'b0;
        end
    end
endmodule
