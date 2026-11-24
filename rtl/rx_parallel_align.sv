`timescale 1ns/1ps
`default_nettype none
// -----------------------------------------------------------------------------
// Word-boundary alignment + elastic skid for the quarter-rate RX word stream.
//
// The gearbox delivers a bit-continuous DATA_W-bit word stream (slips are
// repaired there).  What is still free is where a word starts in the bit
// stream: align_rot rotates the word by align_rot*4 bits (whole UIs), moving
// the word boundary.  A 2-deep skid buffer decouples the producer/consumer
// handshake and absorbs short rate jitter; longer ppm drift is handled at the
// gearbox slip points (drop/insert), which is exactly where an elastic buffer
// would compensate.
//
// Stage B: a real deployment aligns on a known pattern (comma_align style) by
// walking align_rot until the pattern lands on the word boundary; v1 leaves
// align_rot as a control input.
// -----------------------------------------------------------------------------
module rx_parallel_align #(
    parameter int DATA_W = 16
) (
    input  wire               clk_q,
    input  wire               rst_n,
    input  wire               enable,
    input  wire [1:0]         align_rot,      // word rotation in UI units
    input  wire [DATA_W-1:0]  word_in,
    input  wire               word_in_valid,
    output reg  [DATA_W-1:0]  word_out,
    output reg                word_out_valid
);

    // Rotate by whole nibbles (4 bits = 1 UI per step).
    reg [DATA_W-1:0] rot_c;
    always_comb begin
        case (align_rot)
            2'd0: rot_c = word_in;
            2'd1: rot_c = {word_in[DATA_W-5:0], word_in[DATA_W-1:DATA_W-4]};
            2'd2: rot_c = {word_in[DATA_W-9:0], word_in[DATA_W-1:DATA_W-8]};
            default: rot_c = {word_in[DATA_W-13:0], word_in[DATA_W-1:DATA_W-12]};
        endcase
    end

    // 2-deep skid: in -> slot -> out.
    reg [DATA_W-1:0] slot_q;
    reg              slot_v_q;

    always_ff @(posedge clk_q or negedge rst_n) begin
        if (!rst_n) begin
            slot_q         <= '0;
            slot_v_q       <= 1'b0;
            word_out       <= '0;
            word_out_valid <= 1'b0;
        end else begin
            word_out_valid <= 1'b0;
            if (enable) begin
                if (word_in_valid) begin
                    slot_q   <= rot_c;
                    slot_v_q <= 1'b1;
                end
                // Pop to the output one cycle behind the fill, so back-to-back
                // words are held, not dropped.
                if (slot_v_q) begin
                    word_out       <= slot_q;
                    word_out_valid <= 1'b1;
                    slot_v_q       <= 1'b0;
                end
            end else begin
                slot_v_q <= 1'b0;
            end
        end
    end

endmodule
