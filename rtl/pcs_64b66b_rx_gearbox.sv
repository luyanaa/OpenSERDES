`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// 64b/66b RX gearbox: 66-bit block lock on the raw recovered serial stream.
//
// The serial bits arrive LSB-first per block, so after 66 shifts the window
// holds {header[1:0], payload[63:0]} with the sync header in win[65:64].
// Because the block boundary is unknown at reset, the FSM slides the window
// one bit at a time and scores every 66-bit candidate by its sync header:
//   - a candidate with header 01 or 10 is good,
//   - LOCK_BLOCKS consecutive good candidates at the same phase lock the
//     boundary (that phase is then the block boundary),
//   - BAD_BLOCKS consecutive bad headers drop lock and restart the search,
//   - slip_req re-opens the search immediately,
//   - IDLE_TIMEOUT serial_valid-less cycles also drop lock.
//
// While locked, every good 66-bit block produces a one-cycle blk_valid pulse
// with block_out = the raw block (for the descrambler), and a 64-bit payload
// word on out_data/out_valid/out_ready (the sync header is stripped: the
// 64-to-66 rate conversion).  out_ready back-pressure is a one-entry skid;
// a block that arrives while the skid is still full sets overflow and is not
// forwarded.
// -----------------------------------------------------------------------------
module pcs_64b66b_rx_gearbox #(
    parameter int LOCK_BLOCKS  = 4,
    parameter int BAD_BLOCKS   = 4,
    parameter int IDLE_TIMEOUT = 128
) (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        enable,
    input  logic        slip_req,
    input  logic        serial_in,
    input  logic        serial_valid,
    output logic [65:0] block_out,
    output logic        blk_valid,
    output logic        blk_locked,
    output logic [63:0] out_data,
    output logic        out_valid,
    input  logic        out_ready,
    output logic        overflow
);

    // 65 bits of history; win_next is the 66-bit candidate block.
    logic [64:0] win_q;
    logic [65:0] win_next;
    logic [6:0]  bit_cnt_q;      // 0..65; evaluation when it reaches 65
    logic [7:0]  good_cnt_q;
    logic [7:0]  bad_cnt_q;
    logic [15:0] idle_cnt_q;
    logic        locked_q;
    logic        valid_q;
    logic [65:0] block_q;
    logic        out_valid_q;
    logic [63:0] out_data_q;
    logic        ovf_q;

    logic [1:0] hdr;
    logic       good;

    assign win_next = {win_q, serial_in};
    assign hdr      = win_next[65:64];
    assign good     = (hdr == 2'b01) || (hdr == 2'b10);

    assign block_out  = block_q;
    assign blk_valid  = valid_q;
    assign blk_locked = locked_q;
    assign out_data   = out_data_q;
    assign out_valid  = out_valid_q;
    assign overflow   = ovf_q;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            win_q       <= '0;
            bit_cnt_q   <= '0;
            good_cnt_q  <= '0;
            bad_cnt_q   <= '0;
            idle_cnt_q  <= '0;
            locked_q    <= 1'b0;
            valid_q     <= 1'b0;
            block_q     <= '0;
            out_valid_q <= 1'b0;
            out_data_q  <= '0;
            ovf_q       <= 1'b0;
        end else if (!enable) begin
            bit_cnt_q   <= '0;
            good_cnt_q  <= '0;
            bad_cnt_q   <= '0;
            idle_cnt_q  <= '0;
            locked_q    <= 1'b0;
            valid_q     <= 1'b0;
            out_valid_q <= 1'b0;
            ovf_q       <= 1'b0;
        end else begin
            valid_q <= 1'b0;

            // The 64-bit skid must drain even while the line is idle;
            // otherwise a block emitted on the last serial bit of a burst
            // would keep out_valid high with no accompanying blk_valid.
            if (out_valid_q && out_ready)
                out_valid_q <= 1'b0;

            if (slip_req) begin
                // Re-open the search one bit at a time from the next bit.
                locked_q   <= 1'b0;
                good_cnt_q <= '0;
                bad_cnt_q  <= '0;
                bit_cnt_q  <= 7'd65;
                idle_cnt_q <= '0;
            end else if (!serial_valid) begin
                idle_cnt_q <= idle_cnt_q + 1'b1;
                if (locked_q && (idle_cnt_q >= 16'(IDLE_TIMEOUT))) begin
                    locked_q  <= 1'b0;
                    good_cnt_q <= '0;
                    bad_cnt_q  <= '0;
                    // The next bit should be scored immediately.
                    bit_cnt_q <= 7'd65;
                end
            end else begin
                idle_cnt_q <= '0;
                win_q <= win_next[64:0];

                if (bit_cnt_q != 7'd65) begin
                    bit_cnt_q <= bit_cnt_q + 1'b1;
                end else if (good) begin
                    bit_cnt_q <= '0;
                    if (locked_q) begin
                        // Steady state: one block per 66 bits.
                        block_q <= win_next;
                        valid_q <= 1'b1;
                        bad_cnt_q <= '0;
                        if (!out_valid_q || out_ready) begin
                            out_data_q  <= win_next[63:0];
                            out_valid_q <= 1'b1;
                        end else begin
                            ovf_q <= 1'b1;
                        end
                    end else begin
                        good_cnt_q <= good_cnt_q + 1'b1;
                        if (good_cnt_q + 1'b1 >= 8'(LOCK_BLOCKS)) begin
                            locked_q  <= 1'b1;
                            good_cnt_q <= '0;
                            bad_cnt_q  <= '0;
                            block_q   <= win_next;
                            valid_q   <= 1'b1;
                            if (!out_valid_q || out_ready) begin
                                out_data_q  <= win_next[63:0];
                                out_valid_q <= 1'b1;
                            end else begin
                                ovf_q <= 1'b1;
                            end
                        end
                    end
                end else begin
                    // Bad header: keep sliding while hunting, or count down
                    // the lock budget and then slide again.
                    if (locked_q) begin
                        bit_cnt_q <= '0;
                        if (bad_cnt_q + 1'b1 >= 8'(BAD_BLOCKS)) begin
                            locked_q  <= 1'b0;
                            bad_cnt_q <= '0;
                            good_cnt_q <= '0;
                        end else begin
                            bad_cnt_q <= bad_cnt_q + 1'b1;
                        end
                    end else begin
                        good_cnt_q <= '0;
                        bit_cnt_q  <= 7'd65;   // slide by one bit
                    end
                end
            end
        end
    end

endmodule
