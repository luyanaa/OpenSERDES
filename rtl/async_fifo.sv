`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// Dual-clock asynchronous FIFO with Gray-coded pointers.
//
// DEPTH must be a power of two.  The memory is a register array so the module
// is portable to small MPW flows; a RAM macro may be substituted later.
// This is a CDC building block, not a CDC signoff proof: run the target CDC
// checker on the synchronized Gray buses before claiming CDC clean.
// -----------------------------------------------------------------------------
module async_fifo #(
    parameter int DATA_WIDTH   = 32,
    parameter int DEPTH        = 16,
    parameter int SYNC_STAGES  = 2
) (
    input  logic                  wclk,
    input  logic                  wrst_n,
    input  logic                  winc,
    input  logic [DATA_WIDTH-1:0] wdata,
    output logic                  wfull,

    input  logic                  rclk,
    input  logic                  rrst_n,
    input  logic                  rinc,
    output logic [DATA_WIDTH-1:0] rdata,
    output logic                  rempty
);

    localparam int ADDR_W = (DEPTH <= 1) ? 1 : $clog2(DEPTH);
    localparam int PTR_W  = ADDR_W + 1;
    localparam int SYNC_W = (SYNC_STAGES < 2) ? 2 : SYNC_STAGES;

    logic [DATA_WIDTH-1:0] mem [0:DEPTH-1];

    logic [PTR_W-1:0] wbin_q;
    logic [PTR_W-1:0] wgray_q;
    logic [PTR_W-1:0] rbin_q;
    logic [PTR_W-1:0] rgray_q;
    logic [PTR_W-1:0] wbin_next;
    logic [PTR_W-1:0] wgray_next;
    logic [PTR_W-1:0] rbin_next;
    logic [PTR_W-1:0] rgray_next;

    logic [SYNC_W-1:0][PTR_W-1:0] rgray_sync_w;
    logic [SYNC_W-1:0][PTR_W-1:0] wgray_sync_r;
    logic [PTR_W-1:0] rgray_w;
    logic [PTR_W-1:0] wgray_r;

    logic wfull_q;
    logic rempty_q;

    assign wfull  = wfull_q;
    assign rempty = rempty_q;
    assign rdata  = mem[rbin_q[ADDR_W-1:0]];

    assign wbin_next  = wbin_q + ((winc && !wfull_q) ? PTR_W'(1) : PTR_W'(0));
    assign wgray_next = (wbin_next >> 1) ^ wbin_next;
    assign rbin_next  = rbin_q + ((rinc && !rempty_q) ? PTR_W'(1) : PTR_W'(0));
    assign rgray_next = (rbin_next >> 1) ^ rbin_next;

    assign rgray_w = rgray_sync_w[SYNC_W-1];
    assign wgray_r = wgray_sync_r[SYNC_W-1];

    // Write domain.
    always_ff @(posedge wclk or negedge wrst_n) begin
        if (!wrst_n) begin
            wbin_q       <= '0;
            wgray_q      <= '0;
            rgray_sync_w <= '0;
            wfull_q      <= 1'b0;
        end else begin
            rgray_sync_w <= {rgray_sync_w[SYNC_W-2:0], rgray_q};

            if (winc && !wfull_q) begin
                mem[wbin_q[ADDR_W-1:0]] <= wdata;
                wbin_q  <= wbin_next;
                wgray_q <= wgray_next;
            end

            wfull_q <= (wgray_next ==
                        {~rgray_w[PTR_W-1:PTR_W-2], rgray_w[PTR_W-3:0]});
        end
    end

    // Read domain.
    always_ff @(posedge rclk or negedge rrst_n) begin
        if (!rrst_n) begin
            rbin_q       <= '0;
            rgray_q      <= '0;
            wgray_sync_r <= '0;
            rempty_q     <= 1'b1;
        end else begin
            wgray_sync_r <= {wgray_sync_r[SYNC_W-2:0], wgray_q};

            if (rinc && !rempty_q) begin
                rbin_q  <= rbin_next;
                rgray_q <= rgray_next;
            end

            rempty_q <= (rgray_next == wgray_r);
        end
    end

endmodule
