`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// Word-parallel PRBS generator for datapath BIST.
//
// Emits one frame at a time: COMMA followed by WORDS PRBS words.  Each word
// carries DATA_WIDTH consecutive bits of the serial PRBS sequence; within a
// word the first generated bit is the MSB (the serializer may send LSB-first,
// which only reverses the order inside the word, and the checker uses the same
// convention).  The PRBS state restarts from SEED after every comma, so the
// checker can regenerate each frame without cross-frame state.
//
// The generator is paced by in_ready; it holds the current word stable until
// it is accepted.
// -----------------------------------------------------------------------------
module prbs_word_gen #(
    parameter int WIDTH      = 7,
    parameter int DATA_WIDTH = 8,
    parameter int WORDS      = 4,
    parameter logic [WIDTH-1:0]      SEED  = '1,
    parameter logic [DATA_WIDTH-1:0] COMMA = 'hBC
) (
    input  logic                     clk,
    input  logic                     rst_n,
    input  logic                     start,
    input  logic                     clear,
    output logic                     in_valid,
    input  logic                     in_ready,
    output logic [DATA_WIDTH-1:0]    in_word,
    output logic                     in_last
);

    localparam int IDX_W = (WORDS <= 1) ? 1 : $clog2(WORDS + 1);

    logic [WIDTH-1:0] state_q;
    logic [IDX_W-1:0] index_q;
    logic [WIDTH-1:0] next_state;
    logic [DATA_WIDTH-1:0] prbs_word;

    localparam int TAP_HI = (WIDTH == 7)  ? 6  :
                            (WIDTH == 15) ? 14 :
                            (WIDTH == 31) ? 30 : WIDTH - 1;
    localparam int TAP_LO = (WIDTH == 7)  ? 5  :
                            (WIDTH == 15) ? 13 :
                            (WIDTH == 31) ? 27 : WIDTH - 2;


    always_comb begin
        logic [WIDTH-1:0] s;
        logic [DATA_WIDTH-1:0] acc;
        s   = state_q;
        acc = '0;
        for (int k = 0; k < DATA_WIDTH; k++) begin
            acc = {acc[DATA_WIDTH-2:0], s[WIDTH-1]};
            s   = {s[WIDTH-2:0], (s[TAP_HI] ^ s[TAP_LO])};
        end
        prbs_word  = acc;
        next_state = s;
    end

    assign in_word  = (index_q == '0) ? COMMA : prbs_word;
    assign in_valid = start & ~clear;
    assign in_last  = (index_q == IDX_W'(WORDS));

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state_q <= SEED;
            index_q <= '0;
        end else if (clear) begin
            state_q <= SEED;
            index_q <= '0;
        end else if (start && in_ready) begin
            if (index_q == '0) begin
                state_q <= SEED;
                index_q <= IDX_W'(1);
            end else begin
                state_q <= next_state;
                if (index_q == IDX_W'(WORDS))
                    index_q <= '0;
                else
                    index_q <= index_q + 1'b1;
            end
        end
    end

endmodule
