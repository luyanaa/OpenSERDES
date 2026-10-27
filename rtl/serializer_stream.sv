`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// Word-streaming serializer.
//
// This architecture replaces the 256-bit parallel input bank with a narrow
// DATA_WIDTH input and a per-word valid/ready handshake.  It is the
// "word_streaming" capture mode:
//
//   - in_ready is high while the input bank has room.
//   - in_valid && in_ready captures one word at capture_idx_q.
//   - after the last word, serialization starts automatically.
//   - the upstream source only has to hold a word for the accepted cycle; it
//     does NOT have to hold the complete WORDS x DATA_WIDTH frame.
//
// This removes the "ready goes low for WORDS cycles and the external bus must
// remain stable" contract of the parallel capture mode.  The cost is WORDS
// input handshake cycles and a narrower input bus.
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
    logic [BIT_W-1:0]                 bit_sel_q;
    logic [WORD_W-1:0]                word_sel_q;
    logic [WORD_W-1:0]                capture_idx_q;
    logic                             capturing_q;
    logic                             active_q;
    logic                             frame_done_q;
    logic [WORDS-1:0]                 word_bit;
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
    endgenerate

    always_comb begin
        serial_mux = 1'b0;
        for (int w = 0; w < WORDS; w++) begin
            if (word_sel_q == WORD_W'(w))
                serial_mux = word_bit[w];
        end
    end

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
                        active_q     <= 1'b0;
                        capturing_q  <= 1'b1;
                        capture_idx_q<= '0;
                        bit_sel_q    <= '0;
                        word_sel_q   <= '0;
                        frame_done_q <= 1'b1;
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
