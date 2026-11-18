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
// Lock semantics: the first comma after reset/flush produces the `align`
// pulse; `locked` is asserted at that same cycle, after the pulse has been
// delivered.  By default the block then stops watching the payload for
// further commas (they are data-dependent and would cause false slips); an
// external `slip_req` input (a run of code/disparity errors, or loss of CDR
// lock) re-opens the search.  LOCK_COMMAS > 1 requires that many aligned
// commas before locking and is only useful on links that insert repeated
// commas.
//
// RETRIGGER=1 keeps the per-comma `align` pulse alive after lock.  Use it when
// every frame starts with a comma and the receiver needs a fresh symbol
// boundary (and a fresh disparity seed) per frame; a comma inside the payload
// will then realign the frame, so the payload must not contain K28.5.
//
// Bit order: bits arrive on `serial_in` qualified by `serial_valid`.  This
// assumes the link's LSB_FIRST serialisation of the 10-bit symbol (the K28.5
// comma is then the fixed wire sequence 0,0,1,1,1,1,1 / 1,1,0,0,0,0,0).
// -----------------------------------------------------------------------------
module pcs_8b10b_comma #(
    // Number of consecutive aligned commas required to declare lock.
    parameter int LOCK_COMMAS = 2,
    // 1: keep pulsing `align` on every comma after lock (per-frame comma).
    parameter bit RETRIGGER   = 1'b0,
    // Search window: we look for the 7-bit comma followed by enough bits to
    // confirm it sits at a symbol start (bits abcdei fgh.. -> comma is the
    // first 7 bits '0011111' or its inverse '1100000').
    parameter int COMMA_LEN   = 7,
    // Cycles without a comma before the search is declared stuck.  A receiver
    // that never sees K28.5 looks exactly like one that is still searching;
    // with the watchdog the caller gets a definite answer (0 disables).
    parameter int LOCK_TIMEOUT = 65536
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
    output logic comma_seen,   // pulse on each in-boundary comma detection
    // Post-comma running disparity: COMMA_P is the RD- K28.5 variant, so RD
    // becomes +1; COMMA_N is RD+ and RD becomes -1.
    output logic comma_rd_plus,
    // Pulses when LOCK_TIMEOUT cycles pass without a comma while unlocked.
    output logic lock_timeout,
    // Sticky: at least one search window expired since reset/flush.
    output logic lock_stuck
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
    logic                 comma_rd_plus_q;
    logic [LCW-1:0]       comma_cnt_q;
    logic                 locked_q;
    logic [DW-1:0]        delay_q;
    logic                 pending_q;   // comma matched, tail bits still arriving
    logic                 match;
    logic [COMMA_LEN-1:0] window;

    // Sliding window of the last COMMA_LEN bits; serial_in is the newest.
    assign window = {serial_in, hist_q[COMMA_LEN-1:1]};
    assign match  = (window == COMMA_P) || (window == COMMA_N);

    // align fires on the cycle whose serial_in is the symbol's LAST bit, so the
    // NEXT serial bit is the first bit of a fresh 10-bit symbol.
    assign align      = serial_valid & enable & pending_q &
                        (delay_q == DW'(TAIL - 1)) &
                        (RETRIGGER | ~locked_q);
    assign comma_seen = serial_valid & enable & match;
    assign locked     = locked_q;
    assign comma_rd_plus = comma_rd_plus_q;

    // Search watchdog: while unlocked and enabled, a window without any comma
    // means the pattern is not there (wrong polarity policy, dead line, wrong
    // line rate).  The window restarts on every comma so a slow link does not
    // trip it.
    logic [15:0] wd_q;
    logic        stuck_q;
    localparam int WD_W = (LOCK_TIMEOUT <= 1) ? 1 : $clog2(LOCK_TIMEOUT + 1);

    assign lock_timeout = (LOCK_TIMEOUT > 0) && enable && !locked_q &&
                          ((wd_q == 16'(LOCK_TIMEOUT)) ||
                           (idle_wd_q == 16'(LOCK_TIMEOUT)));
    assign lock_stuck   = stuck_q;

    // A dead line (or one carrying data without any K28.5) delivers no comma
    // at all, so the search watchdog counts clk cycles and is only reset by an
    // actual match, not by valid bits.
    logic [15:0] idle_wd_q;
    wire  match_any = serial_valid & enable & match;
    // The idle window expiring is itself the "stuck" event (a silent or
    // comma-less line produces no valid-bit edge to latch it from).
    wire  idle_expired = enable & ~locked_q & (idle_wd_q == 16'(LOCK_TIMEOUT));
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            idle_wd_q <= '0;
        else if (!enable || flush || slip_req || locked_q || match_any)
            idle_wd_q <= '0;
        else if (idle_expired)
            idle_wd_q <= '0;      // restart the window
        else if (idle_wd_q != 16'hFFFF)
            idle_wd_q <= idle_wd_q + 1'b1;
    end

    // Sticky, cleared by reset/flush only (like every other fault flag here).
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            stuck_q <= 1'b0;
        else if (!enable || flush || slip_req)
            stuck_q <= 1'b0;
        else if (idle_expired | (LOCK_TIMEOUT > 0 && locked_q == 1'b0 &&
                                  wd_q == 16'(LOCK_TIMEOUT)))
            stuck_q <= 1'b1;
    end

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            hist_q           <= '0;
            comma_rd_plus_q  <= 1'b0;
            comma_cnt_q      <= '0;
            delay_q          <= '0;
            pending_q        <= 1'b0;
            locked_q         <= 1'b0;
            wd_q             <= '0;
            idle_wd_q        <= '0;
            stuck_q          <= 1'b0;
        end else if (!enable || flush || slip_req) begin
            hist_q           <= '0;
            comma_rd_plus_q  <= 1'b0;
            comma_cnt_q      <= '0;
            delay_q          <= '0;
            pending_q        <= 1'b0;
            locked_q         <= 1'b0;
            wd_q             <= '0;
            idle_wd_q        <= '0;
            stuck_q          <= 1'b0;
        end else if (serial_valid) begin
            hist_q <= window;
            if (LOCK_TIMEOUT > 0) begin
                if (match || locked_q) begin
                    wd_q <= '0;
                end else if (wd_q == 16'(LOCK_TIMEOUT)) begin
                    wd_q <= '0;
                end else begin
                    wd_q <= wd_q + 1'b1;
                end
            end
            if (match) begin
                // (Re)arm the tail counter and latch the comma polarity.  The
                // count is taken at align time, never at match time: locking
                // at match would gate off the align pulse of this very comma.
                comma_rd_plus_q <= (window == COMMA_P);
                pending_q       <= 1'b1;
                delay_q         <= '0;
                if (comma_cnt_q != LCW'(LOCK_COMMAS))
                    comma_cnt_q <= comma_cnt_q + 1'b1;
            end else if (pending_q) begin
                if (delay_q != DW'(TAIL))
                    delay_q <= delay_q + 1'b1;
                if (align) begin
                    pending_q <= 1'b0;
                    if (comma_cnt_q >= LCW'(LOCK_COMMAS))
                        locked_q <= 1'b1;
                end
            end
        end
    end

endmodule
