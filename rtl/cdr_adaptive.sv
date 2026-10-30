`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// Adaptive (frequency-tracking) oversampling CDR.
//
// This is a first-order tracking loop around the fixed-rate phase counter:
//
//   edge -> phase error -> bang-bang loop filter -> freq_trim
//        -> fractional period (occasional +/-1 cycle) -> phase counter
//
// It tracks bounded TX/RX ppm offsets without a phase interpolator.  It is
// deliberately simple: a production CDR should use a proper phase detector,
// loop filter and jitter-tolerance budget.  The RTL contract is:
//
//   - freq_trim is a signed loop state; it is not a calibrated ppm value.
//   - OVERSAMPLE >= 8 is recommended for ppm margin.
//   - The loop is validated for 5000-bit runs at 0 / +/-700 ppm and
//     100 ppm with FRAC_BITS=12.  Jitter tolerance and gate-level equivalence
//     remain open items.
//
// OVERSAMPLE must be >= 4.  OVERSAMPLE == 2 is not supported in adaptive mode.
// -----------------------------------------------------------------------------
module cdr_adaptive #(
    parameter int OVERSAMPLE       = 8,
    parameter int SYNC_STAGES      = 3,
    parameter int FRAC_BITS        = 12,
    parameter int LOOP_GAIN        = 4,
    parameter int LOCK_THRESH      = 8,
    parameter int LOCK_RELEASE     = 8,
    parameter int LOCK_STALE_BITS  = 32,
    parameter int PHASE_DEADBAND   = 1,
    parameter int LOOP_SETTLE_BITS = 32,
    parameter int PHASE_CTRL_W     = (OVERSAMPLE <= 1) ? 1 : $clog2(OVERSAMPLE)
) (
    input  logic clk,
    input  logic rst_n,
    input  logic enable,
    input  logic flush,
    input  logic data_in,
    input  logic [PHASE_CTRL_W-1:0] phase_offset,
    output logic data_out,
    output logic sample_strobe,
    output logic clk_out,
    output logic lock,
    output logic lock_stale,
    output logic signed [FRAC_BITS:0] freq_trim
);

    localparam int N            = (SYNC_STAGES < 2) ? 2 : SYNC_STAGES;
    localparam int PHASE_W      = (OVERSAMPLE + 2 <= 2) ? 1 : $clog2(OVERSAMPLE + 2);
    localparam int HALF         = OVERSAMPLE / 2;
    localparam int BASE_LAST    = OVERSAMPLE - 1;
    localparam int SAMPLE_BASE  = HALF + 1;
    localparam int LOCK_W       = (LOCK_THRESH <= 1) ? 1 : $clog2(LOCK_THRESH + 1);
    localparam int STALE_LIMIT  = (LOCK_STALE_BITS < 1) ? 1 : LOCK_STALE_BITS;
    localparam int STALE_W      = (STALE_LIMIT <= 1) ? 1 : $clog2(STALE_LIMIT + 1);
    localparam int REL_W        = (LOCK_RELEASE <= 1) ? 1 : $clog2(LOCK_RELEASE + 1);
    localparam int SETTLE_W     = (LOOP_SETTLE_BITS <= 1) ? 1 : $clog2(LOOP_SETTLE_BITS + 1);
    localparam int ACC_W        = (LOOP_GAIN <= 1) ? 3 : ($clog2(LOOP_GAIN) + 2);
    localparam int FRAC_W       = FRAC_BITS + 2;
    localparam int FRAC_ONE     = 1 << FRAC_BITS;
    localparam int MAX_TRIM_V   = FRAC_ONE >>> 2;

    localparam logic [PHASE_W-1:0] BASE_LAST_C    = PHASE_W'(BASE_LAST);
    localparam logic [PHASE_W-1:0] SAMPLE_BASE_C  = PHASE_W'(SAMPLE_BASE);
    localparam logic [PHASE_W-1:0] HALF_MINUS_1_C = PHASE_W'((HALF == 0) ? 0 : (HALF - 1));
    localparam logic [PHASE_W-1:0] ONE_C          = PHASE_W'(1);
    localparam logic [PHASE_W-1:0] HALF_C         = PHASE_W'(HALF);
    localparam logic [LOCK_W-1:0]  LOCK_MAX       = LOCK_W'(LOCK_THRESH);
    localparam logic [STALE_W-1:0] STALE_MAX      = STALE_W'(STALE_LIMIT);
    localparam logic [REL_W-1:0]   REL_MAX        = REL_W'(LOCK_RELEASE);
    localparam logic [SETTLE_W-1:0] SETTLE_MAX    = SETTLE_W'(LOOP_SETTLE_BITS);
    localparam logic signed [ACC_W-1:0] ACC_MAX_S = ACC_W'((1 << (ACC_W-1)) - 1);
    localparam logic signed [ACC_W-1:0] LOOP_GAIN_S = ACC_W'(LOOP_GAIN);
    localparam logic signed [FRAC_W-1:0] FRAC_ONE_S = FRAC_W'(FRAC_ONE);
    localparam logic signed [FRAC_BITS:0] MAX_TRIM_S = (FRAC_BITS+1)'(MAX_TRIM_V);
    localparam logic signed [PHASE_W:0] PHASE_OS_S   = (PHASE_W+1)'(OVERSAMPLE);
    localparam logic signed [PHASE_W:0] PHASE_HALF_S = (PHASE_W+1)'(HALF);
    localparam logic signed [PHASE_W:0] DEADBAND_S   = (PHASE_W+1)'(PHASE_DEADBAND);

    logic [N-1:0]       samples;
    logic [PHASE_W-1:0] phase_q;
    logic [PHASE_W-1:0] period_last_q;
    logic [PHASE_W-1:0] period_last_next;
    logic [PHASE_W-1:0] sample_phase;
    logic [PHASE_W:0]   sample_phase_sum;
    logic [PHASE_W-1:0] sample_phase_eff;
    logic [PHASE_W-1:0] clk_high_phase;
    logic signed [1:0]  delta_q;
    logic signed [1:0]  delta_next;
    logic signed [FRAC_W-1:0] frac_q;
    logic signed [FRAC_W-1:0] frac_sum;
    logic signed [FRAC_W-1:0] frac_next;
    logic signed [FRAC_BITS:0] freq_trim_q;
    logic signed [ACC_W-1:0] pd_acc_q;
    logic signed [PHASE_W:0] phase_err;
    logic [LOCK_W-1:0] edge_cnt_q;
    logic [STALE_W-1:0] stale_cnt_q;
    logic [REL_W-1:0]  bad_cnt_q;
    logic [SETTLE_W-1:0] settle_cnt_q;
    logic              data_out_q;
    logic              sample_strobe_q;
    logic              clk_out_q;
    logic              lock_q;
    logic              lock_stale_q;
    logic              data_edge;
    logic              sample_now;
    logic              sample_valid;
    logic              bit_period_end;

    cdr_sync #(.STAGES(N)) u_sync (
        .clk(clk),
        .rst_n(rst_n),
        .async_in(data_in),
        .samples(samples)
    );

    assign data_edge = samples[N-1] ^ samples[N-2];

    always_comb begin
        frac_sum = frac_q + freq_trim_q;
        if (frac_sum >= FRAC_ONE_S) begin
            delta_next = 1;
            frac_next  = frac_sum - FRAC_ONE_S;
        end else if (frac_sum < 0) begin
            delta_next = -1;
            frac_next  = frac_sum + FRAC_ONE_S;
        end else begin
            delta_next = 0;
            frac_next  = frac_sum;
        end
    end

    always_comb begin
        sample_phase = SAMPLE_BASE_C;
        if (delta_q < 0)
            sample_phase = sample_phase - ONE_C;
        else if (delta_q > 0)
            sample_phase = sample_phase + ONE_C;

        period_last_next = BASE_LAST_C;
        if (delta_next < 0)
            period_last_next = period_last_next - ONE_C;
        else if (delta_next > 0)
            period_last_next = period_last_next + ONE_C;

        clk_high_phase = HALF_MINUS_1_C;
        if (delta_q < 0)
            clk_high_phase = clk_high_phase - ONE_C;
        else if (delta_q > 0)
            clk_high_phase = clk_high_phase + ONE_C;

        // Phase-calibration request wraps modulo the variable period space.
        sample_phase_sum = sample_phase + (PHASE_W+1)'(phase_offset);
        if (sample_phase_sum >= (PHASE_W+1)'(OVERSAMPLE + 2))
            sample_phase_eff = PHASE_W'(sample_phase_sum - (PHASE_W+1)'(OVERSAMPLE + 2));
        else
            sample_phase_eff = sample_phase_sum[PHASE_W-1:0];

        if (phase_q <= HALF_C)
            phase_err = $signed({1'b0, phase_q});
        else
            phase_err = $signed({1'b0, phase_q}) - PHASE_OS_S;
    end

    assign sample_now     = (phase_q == sample_phase_eff);
    assign sample_valid   = sample_now && !(data_edge && !lock_q);
    assign bit_period_end = (phase_q == period_last_q);

    assign data_out      = data_out_q;
    assign sample_strobe = sample_strobe_q;
    assign clk_out       = clk_out_q;
    assign lock          = lock_q;
    assign lock_stale    = lock_stale_q;
    assign freq_trim     = freq_trim_q;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            phase_q        <= '0;
            period_last_q  <= BASE_LAST_C;
            delta_q        <= '0;
            frac_q         <= '0;
            freq_trim_q    <= '0;
            pd_acc_q       <= '0;
            edge_cnt_q     <= '0;
            stale_cnt_q    <= '0;
            bad_cnt_q      <= '0;
            settle_cnt_q   <= '0;
            data_out_q     <= 1'b0;
            sample_strobe_q<= 1'b0;
            clk_out_q      <= 1'b0;
            lock_q         <= 1'b0;
            lock_stale_q   <= 1'b0;
        end else if (flush) begin
            phase_q        <= '0;
            period_last_q  <= BASE_LAST_C;
            delta_q        <= '0;
            frac_q         <= '0;
            freq_trim_q    <= '0;
            pd_acc_q       <= '0;
            edge_cnt_q     <= '0;
            stale_cnt_q    <= '0;
            bad_cnt_q      <= '0;
            settle_cnt_q   <= '0;
            data_out_q     <= 1'b0;
            sample_strobe_q<= 1'b0;
            clk_out_q      <= 1'b0;
            lock_q         <= 1'b0;
            lock_stale_q   <= 1'b0;
        end else if (enable) begin
            if (data_edge && !lock_q) begin
                phase_q       <= '0;
                period_last_q <= BASE_LAST_C;
                delta_q       <= '0;
            end else if (bit_period_end) begin
                phase_q       <= '0;
                period_last_q <= period_last_next;
                delta_q       <= delta_next;
                frac_q        <= frac_next;
            end else begin
                phase_q <= phase_q + 1'b1;
            end

            sample_strobe_q <= sample_valid;
            if (sample_valid)
                data_out_q <= samples[N-1];

            if (data_edge) begin
                if (edge_cnt_q != LOCK_MAX)
                    edge_cnt_q <= edge_cnt_q + 1'b1;
                stale_cnt_q  <= '0;
                lock_stale_q <= 1'b0;

                if (lock_q && (settle_cnt_q == 0)) begin
                    if (phase_err > DEADBAND_S) begin
                        if (pd_acc_q < ACC_MAX_S)
                            pd_acc_q <= pd_acc_q + 1'b1;
                    end else if (phase_err < -DEADBAND_S) begin
                        if (pd_acc_q > -ACC_MAX_S)
                            pd_acc_q <= pd_acc_q - 1'b1;
                    end else if (pd_acc_q > 0) begin
                        pd_acc_q <= pd_acc_q - 1'b1;
                    end else if (pd_acc_q < 0) begin
                        pd_acc_q <= pd_acc_q + 1'b1;
                    end

                    if (pd_acc_q >= LOOP_GAIN_S) begin
                        if (freq_trim_q < MAX_TRIM_S)
                            freq_trim_q <= freq_trim_q + (FRAC_BITS+1)'(1);
                        pd_acc_q <= '0;
                    end else if (pd_acc_q <= -LOOP_GAIN_S) begin
                        if (freq_trim_q > -MAX_TRIM_S)
                            freq_trim_q <= freq_trim_q - (FRAC_BITS+1)'(1);
                        pd_acc_q <= '0;
                    end

                    if ((phase_err > PHASE_HALF_S) ||
                        (phase_err < -PHASE_HALF_S) ||
                        ((freq_trim_q >= MAX_TRIM_S) && (phase_err > 0)) ||
                        ((freq_trim_q <= -MAX_TRIM_S) && (phase_err < 0))) begin
                        if (bad_cnt_q != REL_MAX)
                            bad_cnt_q <= bad_cnt_q + 1'b1;
                    end else if (bad_cnt_q != 0) begin
                        bad_cnt_q <= bad_cnt_q - 1'b1;
                    end
                end
            end else if (bit_period_end) begin
                if (stale_cnt_q != STALE_MAX)
                    stale_cnt_q <= stale_cnt_q + 1'b1;
                if ((stale_cnt_q + 1'b1) >= STALE_MAX)
                    lock_stale_q <= 1'b1;
            end

            if (!lock_q && (edge_cnt_q >= LOCK_MAX))
                settle_cnt_q <= SETTLE_MAX;
            else if (bit_period_end && (settle_cnt_q != 0))
                settle_cnt_q <= settle_cnt_q - 1'b1;

            if (lock_q) begin
                if (bad_cnt_q >= REL_MAX) begin
                    lock_q     <= 1'b0;
                    edge_cnt_q <= '0;
                    bad_cnt_q  <= '0;
                    pd_acc_q   <= '0;
                end
            end else begin
                lock_q <= (edge_cnt_q >= LOCK_MAX);
            end

            if (phase_q == clk_high_phase)
                clk_out_q <= 1'b1;
            else if (bit_period_end)
                clk_out_q <= 1'b0;
        end else begin
            sample_strobe_q <= 1'b0;
        end
    end

endmodule
