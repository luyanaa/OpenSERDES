`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// Counter snapshot bridge for cross-domain BIST status.
//
// The PRBS BIST counters live in the BIST (link) clock domain.  Reading a
// free-running 32-bit counter from a different clock domain tears.  This block
// implements a stable-bus snapshot handshake:
//
//   APB domain : a rising edge on req toggles a request line and clears valid.
//   BIST domain: the request toggle is sampled with cdc_sync, the counters and
//                sticky flags are latched together, and the toggle is echoed
//                back as an acknowledge.
//   APB domain : the synchronized acknowledge edge sets valid.  The latched
//                values stay stable until the next request, so the APB domain
//                may read a coherent word.
//
// Software protocol: write the snapshot bit to 1, wait for valid, read the
// counters, then write the snapshot bit to 0 to re-arm the next snapshot.
// pclk and clk may be the same clock; the handshake then simply adds the two
// 2FF stages of latency.
// -----------------------------------------------------------------------------
module bist_snapshot #(
    parameter int WIDTH = 32
) (
    // APB-side interface.
    input  logic             pclk,
    input  logic             presetn,
    input  logic             req,
    output logic             valid,
    output logic [WIDTH-1:0] error_count_o,
    output logic [WIDTH-1:0] bit_count_o,
    output logic             error_o,
    output logic             running_o,

    // BIST/counter-domain interface.
    input  logic             clk,
    input  logic             rst_n,
    input  logic [WIDTH-1:0] error_count_i,
    input  logic [WIDTH-1:0] bit_count_i,
    input  logic             error_i,
    input  logic             running_i
);

    logic req_d;
    logic req_toggle;
    logic req_toggle_clk;
    logic req_toggle_clk_d;
    logic ack_toggle;
    logic ack_toggle_clk;
    logic ack_toggle_clk_d;

    // -------------------------------------------------------------------------
    // APB side: start a snapshot on the rising edge of the request bit.
    // -------------------------------------------------------------------------
    always_ff @(posedge pclk or negedge presetn) begin
        if (!presetn) begin
            req_d      <= 1'b0;
            req_toggle <= 1'b0;
            valid      <= 1'b0;
        end else begin
            req_d <= req;
            if (req && !req_d) begin
                req_toggle <= ~req_toggle;
                valid      <= 1'b0;
            end else if (ack_toggle_clk != ack_toggle_clk_d) begin
                valid <= 1'b1;
            end
        end
    end

    always_ff @(posedge pclk or negedge presetn) begin
        if (!presetn)
            ack_toggle_clk_d <= 1'b0;
        else
            ack_toggle_clk_d <= ack_toggle_clk;
    end

    // -------------------------------------------------------------------------
    // BIST side: latch all counters/flags on a request toggle edge.
    // -------------------------------------------------------------------------
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            req_toggle_clk_d <= 1'b0;
            ack_toggle       <= 1'b0;
            error_count_o    <= '0;
            bit_count_o      <= '0;
            error_o          <= 1'b0;
            running_o        <= 1'b0;
        end else begin
            req_toggle_clk_d <= req_toggle_clk;
            if (req_toggle_clk != req_toggle_clk_d) begin
                error_count_o <= error_count_i;
                bit_count_o   <= bit_count_i;
                error_o       <= error_i;
                running_o     <= running_i;
                ack_toggle    <= req_toggle_clk;
            end
        end
    end

    cdc_sync #(.WIDTH(1)) u_req_sync (
        .clk(clk), .rst_n(rst_n), .d(req_toggle), .q(req_toggle_clk)
    );

    cdc_sync #(.WIDTH(1)) u_ack_sync (
        .clk(pclk), .rst_n(presetn), .d(ack_toggle), .q(ack_toggle_clk)
    );

endmodule
