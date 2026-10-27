`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// Comma / training-pattern detector used for frame alignment.
//
// The detector watches the recovered serial bit stream.  When the last bit of
// PATTERN is present, align is asserted for that bit cycle.  The deserializer
// gives align priority over capture, so the pattern bit is discarded and the
// NEXT valid bit becomes word0 bit0.
//
// locked is sticky until flush or loss of CDR lock.  While locked, further
// pattern matches in the payload are ignored.
//
// PATTERN is the byte/word value in the same bit order as the parallel data.
// For LSB_FIRST=1 the wire order is PATTERN[0], PATTERN[1], ...; for
// LSB_FIRST=0 it is PATTERN[PATTERN_LEN-1], ... PATTERN[0].  The module
// computes the required shift-register image internally.
// -----------------------------------------------------------------------------
module comma_align #(
    parameter int PATTERN_LEN = 8,
    parameter logic [PATTERN_LEN-1:0] PATTERN = 'hBC,
    parameter bit LSB_FIRST  = 1'b1
) (
    input  logic clk,
    input  logic rst_n,
    input  logic enable,
    input  logic flush,
    input  logic rearm,
    input  logic serial_in,
    input  logic serial_valid,
    output logic align,
    output logic locked
);

    // History holds the previous PATTERN_LEN-1 bits; the current serial_in
    // completes the candidate pattern.  PATTERN_LEN must be >= 2.
    localparam int HIST_W = PATTERN_LEN - 1;

    logic [HIST_W-1:0]      history_q;
    logic [PATTERN_LEN-1:0] shift_next;
    logic [PATTERN_LEN-1:0] expected;
    logic                   match;
    logic                   armed;

    generate
        for (genvar i = 0; i < PATTERN_LEN; i++) begin : g_expected
            assign expected[i] = LSB_FIRST ? PATTERN[i]
                                           : PATTERN[PATTERN_LEN-1-i];
        end
    endgenerate

    assign shift_next = {serial_in, history_q};
    assign match      = (shift_next == expected);

    // Combinational so that align is high on the last pattern bit itself.
    // armed allows a per-frame re-alignment (ALIGN_MODE=2) without dropping
    // the sticky locked status.
    assign align = serial_valid & enable & armed & match;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            history_q <= '0;
            locked    <= 1'b0;
            armed     <= 1'b1;
        end else if (flush) begin
            history_q <= '0;
            locked    <= 1'b0;
            armed     <= 1'b1;
        end else if (serial_valid && enable) begin
            history_q <= shift_next[PATTERN_LEN-1:1];
            if (match) begin
                locked <= 1'b1;
                armed  <= 1'b0;
            end else if (rearm) begin
                armed <= 1'b1;
            end
        end else if (rearm) begin
            armed <= 1'b1;
        end
    end

endmodule
