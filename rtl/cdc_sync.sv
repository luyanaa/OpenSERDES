`timescale 1ns/1ps

// Multi-bit 2FF (default) synchronizer.  Each bit is synchronized
// independently; use only for static levels or status bits, not for a coherent
// multi-bit data word.
module cdc_sync #(
    parameter int WIDTH  = 1,
    parameter int STAGES = 2
) (
    input  logic             clk,
    input  logic             rst_n,
    input  logic [WIDTH-1:0] d,
    output logic [WIDTH-1:0] q
);

    localparam int W = (STAGES < 2) ? 2 : STAGES;

    logic [W-1:0][WIDTH-1:0] sync_q;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            sync_q <= '0;
        else
            sync_q <= {sync_q[W-2:0], d};
    end

    assign q = sync_q[W-1];

endmodule
