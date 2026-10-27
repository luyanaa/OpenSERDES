`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// Asynchronous input synchronizer / sampler bank for the CDR.
//
// samples[0] is the newest sample (one clk after data_in); samples[STAGES-1]
// is the oldest, fully settled sample.  The CDR must use the oldest stage for
// both edge detection and data selection so that:
//
//   - edge detection and data selection see the same, settled data history;
//   - increasing STAGES actually increases the metastability settling time of
//     the data path, instead of only the edge-detection path;
//   - the phase counter can compensate for the synchronizer latency with a
//     single, explicit SAMPLE_PHASE offset.
//
// STAGES must be >= 2.  Use >= 3 for a realistic MTBF target; the exact MTBF
// depends on the target PDK flop and the RX clock frequency.
// -----------------------------------------------------------------------------
module cdr_sync #(
    parameter int STAGES = 3
) (
    input  logic              clk,
    input  logic              rst_n,
    input  logic              async_in,
    output logic [STAGES-1:0] samples
);

    localparam int W = (STAGES < 2) ? 2 : STAGES;

    logic [W-1:0] sync_q;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            sync_q <= '0;
        else
            sync_q <= {sync_q[W-2:0], async_in};
    end

    assign samples = sync_q;

endmodule
