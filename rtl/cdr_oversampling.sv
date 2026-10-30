`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// Fixed-rate oversampling CDR (physically explicit sampler -> edge -> select).
//
// This is the "fixed_phase_oversampling" mode.  It acquires the sampling phase
// once from the first edges, then free-runs at OVERSAMPLE cycles per bit.  It
// is intended for source-synchronous or otherwise frequency-locked links.  It
// does NOT track a frequency offset; use cdr_adaptive for that.
//
// Architecture:
//
//   data_in -> cdr_sync (STAGES flops) -> samples[N-1:0]
//                                        |-> edge detect (N-1, N-2)
//                                        |-> data select (N-1)
//   phase counter -> SAMPLE_PHASE -> register data_out/sample_strobe
//
// Both edge detection and data selection use the OLDEST synchronizer stage.
// This makes the data path actually pass through all STAGES flops.  The phase
// counter is loaded at edge detection and sampled at SAMPLE_PHASE, which
// compensates for the synchronizer latency, so the selected sample stays at
// the eye centre for any STAGES >= 2.
//
// OVERSAMPLE >= 4 is the supported mode.  OVERSAMPLE == 2 is a restricted
// mode: it has only two samples per UI and no jitter margin.  The generator
// must mark it explicitly as "os2_restricted"; the RTL still implements it so
// that the existing 200 Mb/s H7CL experiments remain reproducible.
//
// Lock semantics:
//   - lock asserts after LOCK_THRESH detected edges.
//   - lock does NOT decay on long runs of identical bits.  A long run is not
//     evidence of loss of lock.
//   - lock_stale asserts after LOCK_STALE_BITS bit periods without an edge.
//     Consumers may use it as a "no phase evidence" warning, but should not
//     stop capturing data solely because of it.
//   - flush clears acquisition/lock state and restarts the search.
//
// MTBF: increase SYNC_STAGES (>=3 recommended) and verify the synchronizer
// flops are not retimed/merged by the physical flow.  The RTL cannot guarantee
// MTBF by itself.
// -----------------------------------------------------------------------------
module cdr_oversampling #(
    parameter int OVERSAMPLE      = 4,
    parameter int SYNC_STAGES     = 3,
    parameter int LOCK_THRESH     = 8,
    parameter int LOCK_STALE_BITS = 16,
    parameter int PHASE_CTRL_W    = (OVERSAMPLE <= 1) ? 1 : $clog2(OVERSAMPLE)
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
    output logic lock_stale
);

    localparam int N          = (SYNC_STAGES < 2) ? 2 : SYNC_STAGES;
    localparam int PHASE_W    = (OVERSAMPLE <= 1) ? 1 : $clog2(OVERSAMPLE);
    localparam int HALF       = OVERSAMPLE / 2;
    // Edge detection is N-1 cycles behind the pin.  Selecting samples[N-1]
    // and asserting SAMPLE_PHASE = (HALF+1) mod OVERSAMPLE puts the selected
    // sample at edge + HALF cycles for every N.
    localparam int SAMPLE_PHASE = (HALF + 1) % OVERSAMPLE;
    localparam int LAST_PHASE   = OVERSAMPLE - 1;
    localparam int LOCK_W       = (LOCK_THRESH <= 1) ? 1 : $clog2(LOCK_THRESH + 1);
    localparam int STALE_LIMIT  = (LOCK_STALE_BITS < 1) ? 1 : LOCK_STALE_BITS;
    localparam int STALE_W      = (STALE_LIMIT <= 1) ? 1 : $clog2(STALE_LIMIT + 1);

    localparam logic [PHASE_W-1:0] SAMPLE_PHASE_C = PHASE_W'(SAMPLE_PHASE);
    localparam logic [PHASE_W-1:0] LAST_PHASE_C   = PHASE_W'(LAST_PHASE);
    localparam logic [PHASE_W-1:0] HALF_MINUS_1_C = PHASE_W'((HALF == 0) ? 0 : (HALF - 1));
    localparam logic [LOCK_W-1:0]  LOCK_MAX       = LOCK_W'(LOCK_THRESH);
    localparam logic [STALE_W-1:0] STALE_MAX      = STALE_W'(STALE_LIMIT);

    logic [N-1:0]        samples;
    logic [PHASE_W-1:0]  phase_q;
    logic [LOCK_W-1:0]   edge_cnt_q;
    logic [STALE_W-1:0]  stale_cnt_q;
    logic                data_out_q;
    logic                sample_strobe_q;
    logic                clk_out_q;
    logic                lock_q;
    logic                lock_stale_q;
    logic                data_edge;
    logic                sample_now;
    logic                sample_valid;
    logic                bit_period_end;
    logic [PHASE_W:0]    sample_phase_sum;
    logic [PHASE_W-1:0]  sample_phase_eff;

    cdr_sync #(.STAGES(N)) u_sync (
        .clk(clk),
        .rst_n(rst_n),
        .async_in(data_in),
        .samples(samples)
    );

    // External phase calibration request.  The offset is relative to the
    // acquisition-derived sample point and wraps modulo OVERSAMPLE.  A zero
    // default preserves the original behaviour for every existing instantiation.
    always_comb begin
        sample_phase_sum = SAMPLE_PHASE_C + phase_offset;
        if (sample_phase_sum >= (PHASE_W+1)'(OVERSAMPLE))
            sample_phase_eff = PHASE_W'(sample_phase_sum - (PHASE_W+1)'(OVERSAMPLE));
        else
            sample_phase_eff = sample_phase_sum[PHASE_W-1:0];
    end

    assign data_edge      = samples[N-1] ^ samples[N-2];
    assign sample_now     = (phase_q == sample_phase_eff);
    // Do not take a sample on the cycle that reloads the phase from an edge:
    // the selected sample would be from before the acquisition alignment.
    assign sample_valid   = sample_now && !(data_edge && !lock_q);
    assign bit_period_end = (phase_q == LAST_PHASE_C);

    assign data_out      = data_out_q;
    assign sample_strobe = sample_strobe_q;
    assign clk_out       = clk_out_q;
    assign lock          = lock_q;
    assign lock_stale    = lock_stale_q;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            phase_q        <= '0;
            edge_cnt_q     <= '0;
            stale_cnt_q    <= '0;
            data_out_q     <= 1'b0;
            sample_strobe_q<= 1'b0;
            clk_out_q      <= 1'b0;
            lock_q         <= 1'b0;
            lock_stale_q   <= 1'b0;
        end else if (flush) begin
            phase_q        <= '0;
            edge_cnt_q     <= '0;
            stale_cnt_q    <= '0;
            data_out_q     <= 1'b0;
            sample_strobe_q<= 1'b0;
            clk_out_q      <= 1'b0;
            lock_q         <= 1'b0;
            lock_stale_q   <= 1'b0;
        end else if (enable) begin
            // Phase counter.  During acquisition an edge reloads phase 0;
            // after lock the counter free-runs at OVERSAMPLE cycles per bit.
            if (data_edge && !lock_q)
                phase_q <= '0;
            else if (bit_period_end)
                phase_q <= '0;
            else
                phase_q <= phase_q + 1'b1;

            // Data selection uses the oldest, fully settled synchronizer
            // stage.  The phase offset above makes this the eye centre.
            sample_strobe_q <= sample_valid;
            if (sample_valid)
                data_out_q <= samples[N-1];

            // Lock acquisition and stale-edge detection.
            if (data_edge) begin
                if (edge_cnt_q != LOCK_MAX)
                    edge_cnt_q <= edge_cnt_q + 1'b1;
                stale_cnt_q  <= '0;
                lock_stale_q <= 1'b0;
            end else if (bit_period_end) begin
                if (stale_cnt_q != STALE_MAX)
                    stale_cnt_q <= stale_cnt_q + 1'b1;
                if ((stale_cnt_q + 1'b1) >= STALE_MAX)
                    lock_stale_q <= 1'b1;
            end

            lock_q <= (edge_cnt_q >= LOCK_MAX);

            // Divided monitor clock.  Not a functional clock; use
            // sample_strobe to capture data_out.
            if (phase_q == HALF_MINUS_1_C)
                clk_out_q <= 1'b1;
            else if (bit_period_end)
                clk_out_q <= 1'b0;
        end else begin
            sample_strobe_q <= 1'b0;
        end
    end

endmodule
