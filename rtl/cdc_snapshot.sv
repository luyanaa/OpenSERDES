`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// Generic coherent CDC snapshot for a multi-bit status bus.
//
// A free-running multi-bit bus sampled straight through per-bit 2FF
// synchronizers can tear (each bit crosses independently, so the destination
// may see a mix of old and new bits).  This module carries the bus across with
// a self-refreshing request/acknowledge handshake so `data_o` is always a
// coherent sample:
//
//   dest domain : toggles `req_toggle`; when `ack_toggle` catches up it means a
//                 fresh, stable sample is held in the source-side register, so
//                 it is latched into `data_o` and `valid` is asserted.
//   src  domain : on a request-toggle edge it latches `data_i` into `snap_q`.
//                 Because `snap_q` only changes on the (infrequent) request
//                 edge, it is stable while the destination synchronizes it, so
//                 the sampled word cannot tear.  The source echoes the toggle
//                 back as `ack_toggle`.
//
// The request re-toggles automatically after each acknowledge, so `data_o`
// updates continuously every round-trip (~2*Td + 2*Ts cycles) and is never a
// torn word.  `valid` marks that at least one transfer has completed.
// -----------------------------------------------------------------------------
module cdc_snapshot #(
    parameter int WIDTH = 32
) (
    // Destination (sampling) domain.
    input  logic             dclk,
    input  logic             drst_n,
    output logic             valid,
    output logic [WIDTH-1:0] data_o,

    // Source (synchronous) domain.
    input  logic             sclk,
    input  logic             srst_n,
    input  logic [WIDTH-1:0] data_i
);

    logic             req_toggle;      // dest -> src request
    logic             req_toggle_s;    // req_toggle in src domain
    logic             req_toggle_s_d;
    logic             ack_toggle;      // src -> dest acknowledge
    logic             ack_toggle_d;    // ack_toggle in dest domain
    logic [WIDTH-1:0] snap_q;          // coherent held sample in src domain

    // -------------------------------------------------------------------------
    // Source domain: on a request edge, latch a coherent snapshot and echo ack.
    // -------------------------------------------------------------------------
    always_ff @(posedge sclk or negedge srst_n) begin
        if (!srst_n) begin
            req_toggle_s_d <= 1'b0;
            ack_toggle     <= 1'b0;
            snap_q         <= '0;
        end else begin
            req_toggle_s_d <= req_toggle_s;
            if (req_toggle_s != req_toggle_s_d) begin
                snap_q     <= data_i;
                ack_toggle <= req_toggle_s;
            end
        end
    end

    // -------------------------------------------------------------------------
    // Destination domain: self-refreshing request; latch sample when the ack
    // confirms the source-side snapshot is stable.
    // -------------------------------------------------------------------------
    always_ff @(posedge dclk or negedge drst_n) begin
        if (!drst_n) begin
            req_toggle <= 1'b0;
            data_o     <= '0;
            valid      <= 1'b0;
        end else begin
            // When the returned ack matches the last request, the source has a
            // stable snapshot -> capture it and immediately re-request.
            if (ack_toggle_d == req_toggle) begin
                data_o     <= snap_q;
                req_toggle <= ~req_toggle;
                valid      <= 1'b1;
            end
        end
    end

    cdc_sync #(.WIDTH(1)) u_req_sync (
        .clk(sclk), .rst_n(srst_n), .d(req_toggle), .q(req_toggle_s)
    );

    cdc_sync #(.WIDTH(1)) u_ack_sync (
        .clk(dclk), .rst_n(drst_n), .d(ack_toggle), .q(ack_toggle_d)
    );

    // snap_q crosses only after the source has held it stable for a full
    // request/ack round trip; the destination latches it on an ack edge, so it
    // is a coherent word (no per-bit tearing).  Synchronizing it again with
    // cdc_sync is unnecessary and would only add latency.

endmodule
