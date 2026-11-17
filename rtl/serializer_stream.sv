`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// Word-streaming serializer.
//
// This architecture replaces the 256-bit parallel input bank with a narrow
// DATA_WIDTH input and a per-word valid/ready handshake.  It is the
// "word_streaming" capture mode:
//
//   - in_ready is high while the input bank has room.
//   - in_valid && in_ready captures one word per cycle.
//   - after the last word, serialization starts automatically.
//   - the upstream source only has to hold a word for the accepted cycle; it
//     does NOT have to hold the complete WORDS x DATA_WIDTH frame.
//
// Storage is a shift register, not an indexed write.  The word being
// serialized is always data_q[0]; a completed word shifts the next one down.
// This removes the WORDS-way write decoder, the per-word bit-select mux and
// the WORDS-way output mux that an indexed `data_q[word_sel]` read synthesises
// into (the 32x8 case measured 1977 generic cells before this change).
// -----------------------------------------------------------------------------
module serializer_stream #(
    parameter int DATA_WIDTH = 32,
    parameter int WORDS      = 8,
    parameter bit LSB_FIRST  = 1'b1
) (
    input  logic                              clk,
    input  logic                              rst_n,
    input  logic                              enable,
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

    logic [WORDS-1:0][DATA_WIDTH-1:0] data_q;
    logic [WORDS-1:0][DATA_WIDTH-1:0] data_capture;
    logic [WORDS-1:0][DATA_WIDTH-1:0] data_advance;
    logic [BIT_W-1:0]                 bit_sel_q;
    logic [WORD_W-1:0]                word_sel_q;
    logic [WORD_W-1:0]                capture_idx_q;
    logic                             capturing_q;
    logic                             active_q;
    logic                             frame_done_q;
    logic                             serial_mux;

    // WORDS==1 is the continuous Raw PCS mode: the next word can be accepted
    // on the cycle that emits the last bit, so serial_valid has no bubble
    // between words.
    logic last_bit_cycle;
    assign last_bit_cycle = active_q && (word_sel_q == LAST_WORD) &&
                            (bit_sel_q == LAST_BIT);
    assign in_ready     = (capturing_q & ~active_q) |
                          ((WORDS == 1) && last_bit_cycle);
    assign serial_valid = active_q;
    assign busy         = active_q;
    assign frame_done   = frame_done_q;
    assign serial_out   = serial_mux;

    // Shift-register forms.  WORDS==1 has no neighbour to shift from, so it
    // is a plain register instead of a reversed part-select.
    generate
        if (WORDS == 1) begin : g_one_word
            assign data_capture = in_word;
            assign data_advance = '0;
        end else begin : g_shift
            assign data_capture = {in_word, data_q[WORDS-1:1]};
            assign data_advance = {DATA_WIDTH'(0), data_q[WORDS-1:1]};
        end
    endgenerate

    // Operand isolation: the bit mux is held at 0 whenever the block is not
    // emitting a valid bit.  serial_valid = active_q, so this is bit-exact.
    assign serial_mux = active_q & (LSB_FIRST
        ? data_q[0][bit_sel_q]
        : data_q[0][LAST_BIT-bit_sel_q]);

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            data_q       <= '0;
            bit_sel_q    <= '0;
            word_sel_q   <= '0;
            capture_idx_q<= '0;
            capturing_q  <= 1'b1;
            active_q     <= 1'b0;
            frame_done_q <= 1'b0;
        end else begin
            frame_done_q <= 1'b0;

            if (active_q) begin
                if (enable) begin
                    if (word_sel_q == LAST_WORD && bit_sel_q == LAST_BIT) begin
                        frame_done_q <= 1'b1;
                        if ((WORDS == 1) && in_valid) begin
                            // Back-to-back word: reload and keep emitting.
                            data_q     <= data_capture;
                            bit_sel_q  <= '0;
                            word_sel_q <= '0;
                        end else begin
                            active_q     <= 1'b0;
                            capturing_q  <= 1'b1;
                            capture_idx_q<= '0;
                            bit_sel_q    <= '0;
                            word_sel_q   <= '0;
                        end
                    end else if (bit_sel_q == LAST_BIT) begin
                        bit_sel_q  <= '0;
                        word_sel_q <= word_sel_q + 1'b1;
                        // Move the next word to data_q[0].
                        data_q     <= data_advance;
                    end else begin
                        bit_sel_q <= bit_sel_q + 1'b1;
                    end
                end
            end else if (capturing_q && in_valid) begin
                data_q <= data_capture;
                if (capture_idx_q == LAST_WORD) begin
                    capturing_q  <= 1'b0;
                    active_q     <= 1'b1;
                    capture_idx_q<= '0;
                    bit_sel_q    <= '0;
                    word_sel_q   <= '0;
                end else begin
                    capture_idx_q <= capture_idx_q + 1'b1;
                end
            end
        end
    end

endmodule
