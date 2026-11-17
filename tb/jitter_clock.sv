`timescale 1ps/1ps

// -----------------------------------------------------------------------------
// Reference jitter / phase model: the RTL twin of
// verification/channel/phase_model.py.
//
// One "edge" is one rising edge of a sampling clock, i.e. one UI at the
// sampler.  For edge k the model reports the deviation from the nominal edge
// instant, in picoseconds:
//
//   tie_k = ppm_term + sj_term + rj_term + dcd_term
//
//   ppm_term = trunc(-PPM * k * UI_PS / 1e6)
//   sj_term  = (SJ_PEAK_UI_Q * UI_PS * sin_lut(k*inc)) >> (SJ_FRAC_BITS+15)
//              (sin_lut is Q1.15: sin_lut[32] = 32768 = 1.0)
//   rj_term  = (RJ_RMS_UI_Q  * UI_PS * gauss_k)        >> (RJ_FRAC_BITS+16)
//   dcd_term = ((DCD_PEAK_UI_Q * UI_PS) >> DCD_FRAC_BITS) * sign(dcd transition)
//
// and
//
//   gauss_k  = sum of 12 xorshift draws (units 1/65536) - 6     mean 0, var 1
//   sin_lut  = 64-entry Q1.16 quarter-wave sine table
//   inc      = trunc(2^32 * SJ_FREQ_HZ * UI_PS / 1e12)
//
// Both sides use *the same integer arithmetic* (truncating division for the ppm
// term, arithmetic right shifts for the scaled products) so the RTL and the
// Python model agree bit for bit; verification/tests/test_phase_model.py runs
// this module and diffs against the Python output.
//
// The generator is a 16-bit xorshift (x ^= x<<7; x ^= x>>9; x ^= x<<8) with a
// zero seed remapped to 16'hACE1.  The Gaussian is an Irwin-Hall *surrogate*
// because it is exactly reproducible in both languages: no inverse CDF, no
// shared transcendental, and variance exactly 1 from 12 draws.
//
// Timing: one edge every (N_UNIFORM + 1) = 13 `clk` cycles.  `edge_valid`
// pulses together with `tie_ps` and `edge_index` on the 13th cycle.
//
// This is a reference for the digital loops, not a channel: no ISI, no
// reflection, no CTLE/DFE, no SSC modulation profile.
// -----------------------------------------------------------------------------
module jitter_clock #(
    // Nominal UI in ps (100 Mb/s -> 10000).
    parameter int UI_PS        = 10000,
    parameter int SEED         = 16'hACE1,
    // White RJ, RMS in UI, scaled by 2^RJ_FRAC_BITS.
    parameter int RJ_RMS_UI_Q  = 0,
    parameter int RJ_FRAC_BITS = 16,
    // Sinusoidal jitter peak in UI, scaled by 2^SJ_FRAC_BITS.
    parameter int SJ_PEAK_UI_Q  = 0,
    parameter int SJ_FREQ_HZ    = 0,
    parameter int SJ_FRAC_BITS  = 16,
    // Frequency offset in ppm, signed.
    parameter int PPM            = 0,
    // Duty-cycle distortion peak in UI, scaled by 2^DCD_FRAC_BITS.
    parameter int DCD_PEAK_UI_Q = 0,
    parameter int DCD_FRAC_BITS = 16,
    // Number of edges to produce; 0 = free running.
    parameter int N_EDGES       = 0
) (
    input  logic               clk,
    input  logic               rst_n,
    input  logic               enable,
    // Data bit arriving at this edge, for the DCD term.
    input  logic               dcd_bit,
    // One pulse per modelled edge; the three payloads are valid on it.
    output logic               edge_valid,
    output logic [15:0]        edge_index,
    output logic signed [31:0] tie_ps,
    // Running telemetry.
    output logic [31:0]        edge_count,
    output logic signed [31:0] tie_min_ps,
    output logic signed [31:0] tie_max_ps
);
    localparam int N_UNIFORM = 12;

    // ---- 16-bit xorshift ---------------------------------------------------
    logic [15:0] rng_q;
    logic [15:0] xs1, xs2, xs3;
    assign xs1 = rng_q ^ (rng_q << 7);
    assign xs2 = xs1 ^ (xs1 >> 9);
    assign xs3 = xs2 ^ (xs2 << 8);

    // ---- edge scheduling ---------------------------------------------------
    logic [3:0]  cyc_q;         // 0..N_UNIFORM within one edge
    logic [31:0] idx_q;         // edge index k
    logic        done_q;
    logic        valid_q;

    // ---- white jitter ------------------------------------------------------
    logic [19:0] unif_sum_q;    // sum of the 12 draws, unit 1/65536
    logic [19:0] unif_sum_d;    // plus the last draw, for the emit cycle
    logic signed [23:0] gauss;  // (sum - 6) in units 1/65536

    // ---- sinusoidal phase --------------------------------------------------
    logic [31:0] sj_inc;
    logic [31:0] sj_phase_q;    // k*inc, updated once per edge
    logic signed [17:0] sin_val;

    // ---- DCD ---------------------------------------------------------------
    logic              prev_bit_q;
    logic signed [1:0] dcd_sign;

    // ---- TIE ---------------------------------------------------------------
    logic signed [63:0] ppm_term, sj_term, rj_term, dcd_term, tie_calc;
    logic signed [31:0] tie_q;
    logic [15:0]        idx_out_q;

    function automatic logic signed [17:0] sin_lut(input logic [5:0] a);
        case (a)
            6'd0 : sin_lut = 18'sd0;      6'd1 : sin_lut = 18'sd1608;
            6'd2 : sin_lut = 18'sd3212;   6'd3 : sin_lut = 18'sd4808;
            6'd4 : sin_lut = 18'sd6393;   6'd5 : sin_lut = 18'sd7962;
            6'd6 : sin_lut = 18'sd9512;   6'd7 : sin_lut = 18'sd11039;
            6'd8 : sin_lut = 18'sd12540;  6'd9 : sin_lut = 18'sd14010;
            6'd10: sin_lut = 18'sd15447;  6'd11: sin_lut = 18'sd16846;
            6'd12: sin_lut = 18'sd18205;  6'd13: sin_lut = 18'sd19520;
            6'd14: sin_lut = 18'sd20788;  6'd15: sin_lut = 18'sd22006;
            6'd16: sin_lut = 18'sd23170;  6'd17: sin_lut = 18'sd24279;
            6'd18: sin_lut = 18'sd25330;  6'd19: sin_lut = 18'sd26320;
            6'd20: sin_lut = 18'sd27246;  6'd21: sin_lut = 18'sd28106;
            6'd22: sin_lut = 18'sd28900;  6'd23: sin_lut = 18'sd29622;
            6'd24: sin_lut = 18'sd30274;  6'd25: sin_lut = 18'sd30853;
            6'd26: sin_lut = 18'sd31357;  6'd27: sin_lut = 18'sd31786;
            6'd28: sin_lut = 18'sd32138;  6'd29: sin_lut = 18'sd32413;
            6'd30: sin_lut = 18'sd32610;  6'd31: sin_lut = 18'sd32729;
            6'd32: sin_lut = 18'sd32768;  6'd33: sin_lut = 18'sd32729;
            6'd34: sin_lut = 18'sd32610;  6'd35: sin_lut = 18'sd32413;
            6'd36: sin_lut = 18'sd32138;  6'd37: sin_lut = 18'sd31786;
            6'd38: sin_lut = 18'sd31357;  6'd39: sin_lut = 18'sd30853;
            6'd40: sin_lut = 18'sd30274;  6'd41: sin_lut = 18'sd29622;
            6'd42: sin_lut = 18'sd28900;  6'd43: sin_lut = 18'sd28106;
            6'd44: sin_lut = 18'sd27246;  6'd45: sin_lut = 18'sd26320;
            6'd46: sin_lut = 18'sd25330;  6'd47: sin_lut = 18'sd24279;
            6'd48: sin_lut = 18'sd23170;  6'd49: sin_lut = 18'sd22006;
            6'd50: sin_lut = 18'sd20788;  6'd51: sin_lut = 18'sd19520;
            6'd52: sin_lut = 18'sd18205;  6'd53: sin_lut = 18'sd16846;
            6'd54: sin_lut = 18'sd15447;  6'd55: sin_lut = 18'sd14010;
            6'd56: sin_lut = 18'sd12540;  6'd57: sin_lut = 18'sd11039;
            6'd58: sin_lut = 18'sd9512;   6'd59: sin_lut = 18'sd7962;
            6'd60: sin_lut = 18'sd6393;   6'd61: sin_lut = 18'sd4808;
            6'd62: sin_lut = 18'sd3212;   6'd63: sin_lut = 18'sd1608;
            default: sin_lut = 18'sd0;
        endcase
    endfunction

    // 32-bit phase -> Q1.16 sine; quadrant from the top two bits.
    function automatic logic signed [17:0] sin_from_phase(input logic [31:0] p);
        logic [5:0] q;
        begin
            q = p[29:24];
            // Quadrants 0/1 (p[31]=0) read the table forward/backward;
            // quadrants 2/3 negate and mirror the same way.  The two negative
            // arms must mirror the positive ones, not swap them.
            if (!p[31])
                sin_from_phase = p[30] ? sin_lut(6'd63 - q) : sin_lut(q);
            else
                sin_from_phase = p[30] ? -sin_lut(6'd63 - q) : -sin_lut(q);
        end
    endfunction

    // inc = trunc(2^32 * f * UI / 1e12).
    //
    // The direct form overflows 64 bits (2^32 * 5e6 * 1e4 = 2.1e20) and iverilog
    // then silently truncates, so factor 2^32 / 1e12 = 2^20 / 244140625 and
    // split the product into quotient and remainder:
    //
    //   n = f*UI            (fits: f in MHz, UI in ns)
    //   inc = (n/D)*2^20 + ((n%D)*2^20)/D,   D = 244140625
    //
    // Exact for every f*UI <= 1e12, i.e. every increment that fits in 32 bits.
    function automatic logic [31:0] sj_increment(input int freq_hz, input int ui_ps);
        localparam longint D = 64'sd244_140_625;
        localparam longint K = 64'sd1_048_576;
        longint n, q, r, tmp;
        begin
            n = longint'(freq_hz) * longint'(ui_ps);
            q = n / D;
            r = n - q * D;
            tmp = q * K + (r * K) / D;
            sj_increment = tmp[31:0];
        end
    endfunction

    assign sj_inc = sj_increment(SJ_FREQ_HZ, UI_PS);

    // During the emit cycle the 12th draw has already been added to
    // unif_sum_q, so gauss is final there.
    assign unif_sum_d = unif_sum_q;
    assign gauss      = $signed({4'b0, unif_sum_d}) - 24'sd393216;  // 6*65536

    logic [31:0] sj_phase_next;
    assign sj_phase_next = sj_phase_q + sj_inc;

    always_comb begin
        sin_val = sin_from_phase(sj_phase_q);
        dcd_sign = (DCD_PEAK_UI_Q == 0) ? 2'sd0 :
                   (dcd_bit & ~prev_bit_q) ? 2'sd1 :
                   (~dcd_bit & prev_bit_q) ? -2'sd1 : 2'sd0;
    end

    always_comb begin
        ppm_term = -(longint'(PPM) * longint'(idx_q) * longint'(UI_PS))
                   / 64'sd1_000_000;
        sj_term  = (longint'(SJ_PEAK_UI_Q) * longint'(UI_PS)
                    * longint'(sin_val)) >>> (SJ_FRAC_BITS + 15);
        rj_term  = (longint'(RJ_RMS_UI_Q) * longint'(UI_PS)
                    * longint'(gauss)) >>> (RJ_FRAC_BITS + 16);
        dcd_term = ((longint'(DCD_PEAK_UI_Q) * longint'(UI_PS))
                    >>> DCD_FRAC_BITS) * longint'(dcd_sign);
        tie_calc = ppm_term + sj_term + rj_term + dcd_term;
    end

    assign edge_valid = enable & valid_q;
    assign edge_index = idx_out_q;
    assign tie_ps     = tie_q;
    assign edge_count = idx_q;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            rng_q      <= (SEED[15:0] == 16'd0) ? 16'hACE1 : SEED[15:0];
            cyc_q      <= '0;
            idx_q      <= '0;
            done_q     <= 1'b0;
            valid_q    <= 1'b0;
            unif_sum_q <= '0;
            sj_phase_q <= '0;
            prev_bit_q <= 1'b0;
            tie_q      <= '0;
            idx_out_q  <= '0;
            tie_min_ps <= 32'sh7FFF_FFFF;
            tie_max_ps <= 32'sh8000_0000;
        end else if (!enable) begin
            cyc_q      <= '0;
            idx_q      <= '0;
            done_q     <= 1'b0;
            valid_q    <= 1'b0;
            unif_sum_q <= '0;
            sj_phase_q <= '0;
            tie_min_ps <= 32'sh7FFF_FFFF;
            tie_max_ps <= 32'sh8000_0000;
        end else begin
            valid_q <= 1'b0;
            if (!done_q) begin
                if (cyc_q < 4'(N_UNIFORM)) begin
                    rng_q      <= xs3;
                    unif_sum_q <= unif_sum_q + {4'b0, xs3};
                    cyc_q      <= cyc_q + 1'b1;
                end else begin
                    // Emit: unif_sum_q holds all 12 draws, tie_calc is final.
                    tie_q      <= tie_calc[31:0];
                    idx_out_q  <= idx_q[15:0];
                    valid_q    <= 1'b1;
                    idx_q      <= idx_q + 1'b1;
                    sj_phase_q <= sj_phase_next;
                    prev_bit_q <= dcd_bit;
                    unif_sum_q <= '0;
                    cyc_q      <= '0;
                    if (N_EDGES != 0 && idx_q + 1 >= 32'(N_EDGES))
                        done_q <= 1'b1;
                    if ($signed(tie_calc[31:0]) < $signed(tie_min_ps))
                        tie_min_ps <= tie_calc[31:0];
                    if ($signed(tie_calc[31:0]) > $signed(tie_max_ps))
                        tie_max_ps <= tie_calc[31:0];
                end
            end
        end
    end

endmodule
