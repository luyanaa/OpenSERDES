`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// Word-streaming serializer with a 2:1 hierarchical word-select tree.
//
// The capture and handshake contract is identical to serializer_stream:
//   in_ready is high while the input bank has room, WORDS words are captured,
//   then DATA_WIDTH bits per word are serialized one bit per clk.
//
// The difference is the final word mux: instead of a flat WORDS-way one-hot
// mux, the word bits are reduced by a balanced tree of 2:1 muxes.  For
// WORDS=8 this is 3 levels of 2:1 muxes (4 -> 2 -> 1) rather than one 8-input
// select with 8 one-hot terms.  The bit selector inside each word is unchanged.
//
// This is a structural first step, not a half-rate/DDR architecture: the final
// output still changes once per clk.  Half-rate requires clock-phase generation
// and a different capture/retiming contract.
// -----------------------------------------------------------------------------
module serializer_hier_2to1 #(
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
    localparam int TREE     = 1 << WORD_W;              // power-of-two leaves
    localparam int LEVELS   = (TREE <= 1) ? 0 : WORD_W;
    localparam logic [BIT_W-1:0]  LAST_BIT  = BIT_W'(DATA_WIDTH-1);
    localparam logic [WORD_W-1:0] LAST_WORD = WORD_W'(WORDS-1);

    logic [WORDS-1:0][DATA_WIDTH-1:0] data_q;
    logic [BIT_W-1:0]                 bit_sel_q;
    logic [WORD_W-1:0]                word_sel_q;
    logic [WORD_W-1:0]                capture_idx_q;
    logic                             capturing_q;
    logic                             active_q;
    logic                             frame_done_q;
    logic [WORDS-1:0]                 word_bit;
    logic [TREE-1:0]                  leaf;
    logic                             serial_mux;

    assign in_ready     = capturing_q & ~active_q;
    assign serial_valid = active_q;
    assign busy         = active_q;
    assign frame_done   = frame_done_q;
    assign serial_out   = serial_mux;

    generate
        for (genvar w = 0; w < WORDS; w++) begin : g_word_bit
            assign word_bit[w] = LSB_FIRST
                ? data_q[w][bit_sel_q]
                : data_q[w][LAST_BIT-bit_sel_q];
        end
        for (genvar w = 0; w < TREE; w++) begin : g_leaf
            if (w < WORDS) begin : g_present
                assign leaf[w] = word_bit[w];
            end else begin : g_pad
                assign leaf[w] = 1'b0;
            end
        end
    endgenerate

    // Balanced 2:1 reduction.  Keeping the intermediate vector procedural
    // avoids a generate array that some linters flag as combinational feedback.
    always_comb begin
        logic [TREE-1:0] current;
        logic [TREE-1:0] next;
        current = leaf;
        for (int level = 0; level < LEVELS; level++) begin
            next = '0;
            for (int i = 0; i < (TREE >> (level + 1)); i++)
                next[i] = word_sel_q[level]
                    ? current[2*i+1] : current[2*i];
            current = next;
        end
        serial_mux = current[0];
    end

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            data_q        <= '0;
            bit_sel_q     <= '0;
            word_sel_q    <= '0;
            capture_idx_q <= '0;
            capturing_q   <= 1'b1;
            active_q      <= 1'b0;
            frame_done_q  <= 1'b0;
        end else begin
            frame_done_q <= 1'b0;

            if (active_q) begin
                if (enable) begin
                    if (word_sel_q == LAST_WORD && bit_sel_q == LAST_BIT) begin
                        active_q      <= 1'b0;
                        capturing_q   <= 1'b1;
                        capture_idx_q <= '0;
                        bit_sel_q     <= '0;
                        word_sel_q    <= '0;
                        frame_done_q  <= 1'b1;
                    end else if (bit_sel_q == LAST_BIT) begin
                        bit_sel_q  <= '0;
                        word_sel_q <= word_sel_q + 1'b1;
                    end else begin
                        bit_sel_q <= bit_sel_q + 1'b1;
                    end
                end
            end else if (capturing_q && in_valid) begin
                data_q[capture_idx_q] <= in_word;
                if (capture_idx_q == LAST_WORD) begin
                    capturing_q   <= 1'b0;
                    active_q      <= 1'b1;
                    capture_idx_q <= '0;
                    bit_sel_q     <= '0;
                    word_sel_q    <= '0;
                end else begin
                    capture_idx_q <= capture_idx_q + 1'b1;
                end
            end
        end
    end

endmodule
