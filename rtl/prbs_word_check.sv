`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// Word-parallel PRBS checker for datapath BIST.
//
// Streaming (AXIS-like) checker: one payload word per word_valid beat, with
// word_last marking the final payload word of a frame.  The transmitter
// restarts the PRBS sequence after every comma, so the first payload word of
// each frame is compared against SEED -> word 0.  A COMMA word resets the
// expected-word index (useful for serial loopback tests); the normal SoC path
// presents payload words only, because the RX core strips the comma.
//
// error_count counts bit errors; frame_count counts completed frames.  The
// sticky error is cleared only by clear/reset.
// -----------------------------------------------------------------------------
module prbs_word_check #(
    parameter int WIDTH      = 7,
    parameter int DATA_WIDTH = 8,
    parameter int WORDS      = 4,
    parameter logic [WIDTH-1:0]      SEED  = '1,
    parameter logic [DATA_WIDTH-1:0] COMMA = 'hBC,
    // When set, a COMMA word resets the expected-word index and is not
    // compared (serial-loopback mode).  The RX AXIS path has no comma word,
    // so the SoC wrapper leaves this at 0.
    parameter bit SKIP_COMMA = 1'b0
) (
    input  logic                     clk,
    input  logic                     rst_n,
    input  logic                     clear,
    input  logic                     word_valid,
    input  logic                     word_last,
    input  logic [DATA_WIDTH-1:0]    word_in,
    output logic                     error,
    output logic [31:0]              error_count,
    output logic [31:0]              frame_count
);

    localparam int FRAME_BITS = WORDS * DATA_WIDTH;
    localparam int IDX_W      = (WORDS <= 1) ? 1 : $clog2(WORDS);
    localparam int TAP_HI = (WIDTH == 7)  ? 6  :
                            (WIDTH == 15) ? 14 :
                            (WIDTH == 31) ? 30 : WIDTH - 1;
    localparam int TAP_LO = (WIDTH == 7)  ? 5  :
                            (WIDTH == 15) ? 13 :
                            (WIDTH == 31) ? 27 : WIDTH - 2;

    logic [FRAME_BITS-1:0] expected_flat;
    logic [IDX_W-1:0]      index_q;
    logic error_q;
    logic [31:0] error_count_q;
    logic [31:0] frame_count_q;

    always_comb begin
        logic [WIDTH-1:0] s;
        s = SEED;
        expected_flat = '0;
        for (int w = 0; w < WORDS; w++) begin
            for (int k = 0; k < DATA_WIDTH; k++) begin
                expected_flat[w*DATA_WIDTH + (DATA_WIDTH-1-k)] = s[WIDTH-1];
                s = {s[WIDTH-2:0], (s[TAP_HI] ^ s[TAP_LO])};
            end
        end
    end

    // Word index 0 is the first payload word after the comma.
    logic [DATA_WIDTH-1:0] expected_word;
    assign expected_word = expected_flat[index_q*DATA_WIDTH +: DATA_WIDTH];

    assign error       = error_q;
    assign error_count = error_count_q;
    assign frame_count = frame_count_q;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            index_q       <= '0;
            error_q       <= 1'b0;
            error_count_q <= '0;
            frame_count_q <= '0;
        end else if (clear) begin
            index_q       <= '0;
            error_q       <= 1'b0;
            error_count_q <= '0;
            frame_count_q <= '0;
        end else if (word_valid) begin
            logic [DATA_WIDTH-1:0] diff;
            int bits;
            if (SKIP_COMMA && word_in == COMMA) begin
                index_q <= '0;
            end else begin
                diff = word_in ^ expected_word;
                bits = 0;
                for (int i = 0; i < DATA_WIDTH; i++)
                    bits = bits + int'(diff[i]);
                if (bits != 0) begin
                    error_q       <= 1'b1;
                    error_count_q <= error_count_q + 32'(bits);
                end
                if (word_last) begin
                    frame_count_q <= frame_count_q + 1'b1;
                    index_q       <= '0;
                end else begin
                    index_q <= index_q + 1'b1;
                end
            end
        end
    end

endmodule
