`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// Async-assert / sync-deassert reset synchronizer.
//
// All OpenSERDES abstract blocks use an asynchronous active-low reset.  The
// top level should drive each clock domain through this primitive so that
// reset release is synchronous to that domain.  Keeping the synchronizer out
// of the leaf blocks makes them composable and keeps reset polarity consistent.
// -----------------------------------------------------------------------------
module reset_sync #(
    parameter int STAGES = 2
) (
    input  logic clk,
    input  logic rst_n_in,
    output logic rst_n_out
);

    localparam int W = (STAGES < 2) ? 2 : STAGES;

    logic [W-1:0] sync_q;

    always_ff @(posedge clk or negedge rst_n_in) begin
        if (!rst_n_in)
            sync_q <= '0;
        else
            sync_q <= {sync_q[W-2:0], 1'b1};
    end

    assign rst_n_out = sync_q[W-1];

endmodule
