`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// 8b/10b K28.5 comma detect and symbol-boundary realignment.
//
// The K28.5 code word contains the unique 7-bit "comma" 0011111 (or its
// complement 1100000) which cannot appear in any valid 10-bit symbol nor span
// the boundary of two valid symbols.  This block slides a 10-bit window over
// the recovered serial stream and looks for the comma in the position where
// bit a..g would start a symbol.  On a match it asserts `align` for that bit
// cycle so the downstream deserializer treats the NEXT serial bit as the start
// of a fresh 10-bit symbol (same contract as comma_align).
//
// Lock semantics: after `LOCK_COMMAS` comma detections at the same boundary the
// block declares `locked`.  It does NOT watch the payload for further commas
// (they are data-dependent and would cause false slips); instead an external
// `slip_req` input (e.g. a run of code/disparity errors, or loss of CDR lock)
// re-opens the search.
//
// Bit order: bits arrive on `serial_in` qualified by `serial_valid`.  This
// assumes the link's LSB_FIRST serialisation of the 10-bit symbol (the K28.5
// comma is then the fixed wire sequence 0,0,1,1,1,1,1 / 1,1,0,0,0,0,0).
// -----------------------------------------------------------------------------
module pcs_8b10b_comma #(
    // Number of consecutive aligned commas required to declare lock.
    parameter int LOCK_COMMAS = 2,
    // Search window: we look for the 7-bit comma followed by enough bits to
    // confirm it sits at a symbol start (bits abcdei fgh.. -> comma is the
    // first 7 bits '0011111' or its inverse '1100000').
    parameter int COMMA_LEN   = 7
) (
    input  logic clk,
    input  logic rst_n,
    input  logic enable,
    input  logic flush,        // drop lock and re-search
    input  logic serial_in,
    input  logic serial_valid,
    input  logic slip_req,     // external re-lock request (decode-error burst)
    output logic align,        // pulse: next bit starts a new symbol
    output logic locked,       // sticky comma lock
    output logic comma_seen    // pulse on each in-boundary comma detection
);

    // 7-bit comma patterns.  The K28.5 symbol (10'b0101111100 / 1010000011)
    // is serialised low-bit-first, so the wire emits 0,0,1,1,1,1,1,... for
    // the negative-disparity comma.  `window` holds the newest bit in its MSB,
    // so the 7-bit image is the transmitted sequence reversed: b0..b6 =
    // 0,0,1,1,1,1,1 -> window = 1111100 ; the inverse comma gives 0000011.
    localparam logic [COMMA_LEN-1:0] COMMA_P = 7'b1111100;
    localparam logic [COMMA_LEN-1:0] COMMA_N = 7'b0000011;

    // The comma occupies the first COMMA_LEN bits of a 10-bit symbol.  We
    // detect its last comma bit, then wait for the remaining (10-COMMA_LEN)
    // symbol bits to pass before asserting `align` so the NEXT serial bit is
    // the start of the following symbol.  `delay_q` counts those tail bits.
    localparam int TAIL = 10 - COMMA_LEN;            // 3 bits after the comma
    localparam int LCW  = (LOCK_COMMAS <= 1) ? 1 : $clog2(LOCK_COMMAS + 1);
    localparam int DW   = (TAIL <= 1) ? 1 : $clog2(TAIL + 1);

    logic [COMMA_LEN-1:0] hist_q;
    logic [LCW-1:0]       comma_cnt_q;
    logic                 locked_q;
    logic [DW-1:0]        delay_q;
    logic                 match;
    logic [COMMA_LEN-1:0] window;

    // Sliding window of the last COMMA_LEN bits; serial_in is the newest.
    assign window = {serial_in, hist_q[COMMA_LEN-1:1]};
    assign match  = (window == COMMA_P) || (window == COMMA_N);

    // align fires when the symbol that began with the comma finishes, so the
    // NEXT serial bit is the first bit of a fresh symbol.
    assign align      = serial_valid & enable & (delay_q == DW'(TAIL - 1)) & ~locked_q;
    assign comma_seen = serial_valid & enable & match;
    assign locked     = locked_q;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            hist_q      <= '0;
            comma_cnt_q <= '0;
            delay_q     <= '0;
            locked_q    <= 1'b0;
        end else if (!enable || flush || slip_req) begin
            hist_q      <= '0;
            comma_cnt_q <= '0;
            delay_q     <= '0;
            locked_q    <= 1'b0;
        end else if (serial_valid) begin
            hist_q <= window;
            if (match) begin
                delay_q <= '0;
                if (comma_cnt_q < LCW'(LOCK_COMMAS))
                    comma_cnt_q <= comma_cnt_q + 1'b1;
                if (comma_cnt_q >= LCW'(LOCK_COMMAS - 1))
                    locked_q <= 1'b1;
            end else if (delay_q != DW'(TAIL)) begin
                delay_q <= delay_q + 1'b1;
            end
        end
    end

endmodule
