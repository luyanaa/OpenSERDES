`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// Half-rate RX pair deserializer prototype.
//
// The sampler/front-end is outside this module: it delivers two serial bits per
// clk_word (bit0 first, bit1 second).  The word bank, bit/word counters and
// par_valid generation run at clk_word, so the wide parallel path is not
// clocked at the bit rate.
//
// This is the RX counterpart to serializer_half_rate; the actual DDR/2-phase
// sampler, CDR and comma alignment are separate follow-ups.
// -----------------------------------------------------------------------------
module deserializer_half_rate #(
    parameter int DATA_WIDTH = 32,
    parameter int WORDS      = 8,
    parameter bit LSB_FIRST  = 1'b1
) (
    input  logic                              clk_word,
    input  logic                              rst_n,
    input  logic                              enable,
    input  logic                              clear,
    input  logic                              pair_valid,
    input  logic                              bit0,
    input  logic                              bit1,
    output logic [WORDS-1:0][DATA_WIDTH-1:0] par_data,
    output logic                             par_valid,
    output logic                             busy
);

    localparam int BIT_W  = (DATA_WIDTH <= 1) ? 1 : $clog2(DATA_WIDTH);
    localparam int WORD_W = (WORDS <= 1) ? 1 : $clog2(WORDS);
    localparam logic [BIT_W-1:0]  LAST_BIT  = BIT_W'(DATA_WIDTH-1);
    localparam logic [WORD_W-1:0] LAST_WORD = WORD_W'(WORDS-1);

    logic [DATA_WIDTH-1:0] word_q [0:WORDS-1];
    logic [BIT_W-1:0]  bit_idx_q;
    logic [WORD_W-1:0] word_idx_q;
    logic              busy_q;
    logic              par_valid_q;

    generate
        for (genvar w = 0; w < WORDS; w++) begin : g_out
            assign par_data[w] = word_q[w];
        end
    endgenerate

    assign par_valid  = par_valid_q;
    assign busy       = busy_q;

    always_ff @(posedge clk_word or negedge rst_n) begin
        if (!rst_n) begin
            for (int w = 0; w < WORDS; w++)
                word_q[w] <= '0;
            bit_idx_q   <= '0;
            word_idx_q  <= '0;
            busy_q      <= 1'b0;
            par_valid_q <= 1'b0;
        end else begin
            par_valid_q <= 1'b0;
            if (clear) begin
                bit_idx_q  <= '0;
                word_idx_q <= '0;
                busy_q     <= 1'b0;
            end else if (enable && pair_valid) begin
                busy_q <= 1'b1;
                if (bit_idx_q == LAST_BIT) begin
                    // Odd-width tail: only bit0 belongs to this word.
                    word_q[word_idx_q][LSB_FIRST ? int'(bit_idx_q)
                                                 : (DATA_WIDTH-1-int'(bit_idx_q))] <= bit0;
                    bit_idx_q <= '0;
                    if (word_idx_q == LAST_WORD) begin
                        word_idx_q  <= '0;
                        busy_q      <= 1'b0;
                        par_valid_q <= 1'b1;
                    end else begin
                        word_idx_q <= word_idx_q + 1'b1;
                    end
                end else if (bit_idx_q == LAST_BIT - 1'b1) begin
                    // Even-width final pair: both bits complete the word.
                    word_q[word_idx_q][LSB_FIRST ? int'(bit_idx_q)
                                                 : (DATA_WIDTH-1-int'(bit_idx_q))] <= bit0;
                    word_q[word_idx_q][LSB_FIRST ? int'(bit_idx_q)+1
                                                 : (DATA_WIDTH-2-int'(bit_idx_q))] <= bit1;
                    bit_idx_q <= '0;
                    if (word_idx_q == LAST_WORD) begin
                        word_idx_q  <= '0;
                        busy_q      <= 1'b0;
                        par_valid_q <= 1'b1;
                    end else begin
                        word_idx_q <= word_idx_q + 1'b1;
                    end
                end else begin
                    word_q[word_idx_q][LSB_FIRST ? int'(bit_idx_q)
                                                 : (DATA_WIDTH-1-int'(bit_idx_q))] <= bit0;
                    word_q[word_idx_q][LSB_FIRST ? int'(bit_idx_q)+1
                                                 : (DATA_WIDTH-2-int'(bit_idx_q))] <= bit1;
                    bit_idx_q <= bit_idx_q + 2;
                end
            end
        end
    end

endmodule
