`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// Half-rate word-streaming serializer prototype.
//
// Parallel capture and word/bit bookkeeping run on clk_word; the final bit
// selection runs on clk_bit = 2 * clk_word.  The two clocks must be generated
// from the same source and aligned (clk_word is the divide-by-2 word clock).
//
// Handshake:
//   - in_ready is high in the clk_word domain while the bank has room.
//   - after the last word is captured, a level request crosses into the bit
//     domain through a 2FF synchronizer.
//   - the bit domain emits one bit per clk_bit and returns a done toggle.
//
// This is a structural prototype: it is not yet wired into the SoC/generator.
// The clock-phase contract and reset/deassert ordering are explicit parts of
// the interface, not hidden RTL assumptions.
// -----------------------------------------------------------------------------
module serializer_half_rate #(
    parameter int DATA_WIDTH = 32,
    parameter int WORDS      = 8,
    parameter bit LSB_FIRST  = 1'b1
) (
    input  logic                              clk_word,
    input  logic                              clk_bit,
    input  logic                              rst_n,
    input  logic                              in_valid,
    output logic                              in_ready,
    input  logic [DATA_WIDTH-1:0]             in_word,
    output logic                              serial_out,
    output logic                              serial_valid,
    output logic                              busy,
    output logic                              frame_done
);

    localparam int BIT_W    = (DATA_WIDTH <= 1) ? 1 : $clog2(DATA_WIDTH);
    localparam int WORD_W   = (WORDS <= 1) ? 1 : $clog2(WORDS);
    localparam logic [BIT_W-1:0]  LAST_BIT  = BIT_W'(DATA_WIDTH-1);
    localparam logic [WORD_W-1:0] LAST_WORD = WORD_W'(WORDS-1);

    // Word domain.
    logic [WORDS-1:0][DATA_WIDTH-1:0] data_q;
    logic [WORD_W-1:0] capture_idx_q;
    logic              capturing_q;
    logic              active_word_q;
    logic              start_req;
    logic              done_sync1;
    logic              done_sync2;
    logic              done_seen;

    // Bit domain.
    logic              start_sync1;
    logic              start_sync2;
    logic              start_seen;
    logic              active_bit_q;
    logic              serial_q;
    logic              serial_valid_q;
    logic [WORD_W-1:0] word_sel_q;
    logic [BIT_W-1:0]  bit_sel_q;
    logic [DATA_WIDTH-1:0] current_word;
    logic              done_toggle;

    assign in_ready     = capturing_q & ~active_word_q;
    assign serial_out   = serial_q;
    assign serial_valid = serial_valid_q;
    assign busy         = active_bit_q;
    assign frame_done   = active_bit_q && (word_sel_q == LAST_WORD) &&
                          (bit_sel_q == LAST_BIT);

    assign current_word = data_q[word_sel_q];

    // -------------------------------------------------------------------------
    // Word-clock domain: capture WORDS words.  start_req is a level that stays
    // high until the bit domain finishes and returns done.
    // -------------------------------------------------------------------------
    always_ff @(posedge clk_word or negedge rst_n) begin
        if (!rst_n) begin
            data_q        <= '0;
            capture_idx_q <= '0;
            capturing_q   <= 1'b1;
            active_word_q <= 1'b0;
            start_req     <= 1'b0;
            done_sync1    <= 1'b0;
            done_sync2    <= 1'b0;
            done_seen     <= 1'b0;
        end else begin
            done_sync1 <= done_toggle;
            done_sync2 <= done_sync1;

            if (done_sync2 != done_seen) begin
                done_seen     <= done_sync2;
                active_word_q <= 1'b0;
                start_req     <= 1'b0;
                capturing_q   <= 1'b1;
                capture_idx_q <= '0;
            end else if (in_valid && in_ready) begin
                data_q[capture_idx_q] <= in_word;
                if (capture_idx_q == LAST_WORD) begin
                    capturing_q   <= 1'b0;
                    active_word_q <= 1'b1;
                    start_req     <= 1'b1;
                    capture_idx_q <= '0;
                end else begin
                    capture_idx_q <= capture_idx_q + 1'b1;
                end
            end
        end
    end

    // -------------------------------------------------------------------------
    // Bit-clock domain: start on the synchronized request edge, then emit one
    // bit per clk_bit.  data_q is stable before start_req is asserted.
    // -------------------------------------------------------------------------
    always_ff @(posedge clk_bit or negedge rst_n) begin
        if (!rst_n) begin
            start_sync1      <= 1'b0;
            start_sync2      <= 1'b0;
            start_seen       <= 1'b0;
            active_bit_q     <= 1'b0;
            serial_q         <= 1'b0;
            serial_valid_q   <= 1'b0;
            word_sel_q       <= '0;
            bit_sel_q        <= '0;
            done_toggle      <= 1'b0;
        end else begin
            start_sync1 <= start_req;
            start_sync2 <= start_sync1;

            if (!start_sync2)
                start_seen <= 1'b0;
            else if (!start_seen) begin
                start_seen   <= 1'b1;
                active_bit_q <= 1'b1;
                word_sel_q   <= '0;
                bit_sel_q    <= '0;
            end

            if (active_bit_q) begin
                serial_q       <= LSB_FIRST
                    ? current_word[bit_sel_q]
                    : current_word[BIT_W'(DATA_WIDTH-1)-bit_sel_q];
                serial_valid_q <= 1'b1;

                if (word_sel_q == LAST_WORD && bit_sel_q == LAST_BIT) begin
                    active_bit_q   <= 1'b0;
                    serial_valid_q <= 1'b1;
                    done_toggle    <= ~done_toggle;
                end else if (bit_sel_q == LAST_BIT) begin
                    bit_sel_q  <= '0;
                    word_sel_q <= word_sel_q + 1'b1;
                end else begin
                    bit_sel_q <= bit_sel_q + 1'b1;
                end
            end else begin
                serial_valid_q <= 1'b0;
            end
        end
    end

endmodule
