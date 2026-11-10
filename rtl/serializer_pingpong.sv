`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// Ping-pong register-bank serializer.
//
// Drop-in interface-compatible with serializer (load / ready / par_data), but
// instead of one WORDS x DATA_WIDTH bank it has TWO.  While bank A is being
// serialized, the next frame can be captured into bank B, so ready does not go
// low for WORDS capture cycles on back-to-back frames.
//
// Trade-off (measured with Yosys generic gates on 8x4 / 32x8):
//   - area: ~2x the input bank (one extra WORDS x DATA_WIDTH register group)
//   - latency: one start cycle after capture, instead of WORDS capture cycles
//   - throughput: back-to-back frames stream continuously
//
// The capture write is still a single-cycle full-frame load into the free bank,
// so the load-enable fanout is the same as serializer CAPTURE_MODE=0; the
// difference is the extra bank and the ready/occupancy logic.
// -----------------------------------------------------------------------------
module serializer_pingpong #(
    parameter int DATA_WIDTH = 32,
    parameter int WORDS      = 8,
    parameter bit LSB_FIRST  = 1'b1
) (
    input  logic                              clk,
    input  logic                              rst_n,
    input  logic                              enable,
    input  logic                              load,
    input  logic [WORDS-1:0][DATA_WIDTH-1:0]  par_data,
    output logic                              ready,
    output logic                              serial_out,
    output logic                              serial_valid,
    output logic                              busy,
    output logic                              frame_done
);

    localparam int BIT_W    = (DATA_WIDTH <= 1) ? 1 : $clog2(DATA_WIDTH);
    localparam int WORD_W   = (WORDS <= 1) ? 1 : $clog2(WORDS);
    localparam logic [BIT_W-1:0]  LAST_BIT  = BIT_W'(DATA_WIDTH-1);
    localparam logic [WORD_W-1:0] LAST_WORD = WORD_W'(WORDS-1);

    logic [WORDS-1:0][DATA_WIDTH-1:0] bank0_q;
    logic [WORDS-1:0][DATA_WIDTH-1:0] bank1_q;
    logic [1:0]  have_q;
    logic        cur_q;
    logic        busy_q;
    logic        frame_done_q;
    logic [BIT_W-1:0]  bit_sel_q;
    logic [WORD_W-1:0] word_sel_q;
    logic [WORDS-1:0]  word_bit;
    logic              serial_mux;
    logic              wr_bank;
    logic              wr_en;
    logic              start;
    logic              start_bank;

    // Write into the bank that is not currently being serialized.  When idle,
    // pick any free bank.
    assign wr_bank  = busy_q ? ~cur_q : (have_q[0] ? 1'b1 : 1'b0);
    assign ready    = ~have_q[wr_bank];
    assign wr_en    = load & ready;

    assign start      = ~busy_q & (have_q[0] | have_q[1]);
    assign start_bank = have_q[0] ? 1'b0 : 1'b1;

    // Selected bank drives the word mux.
    generate
        for (genvar w = 0; w < WORDS; w++) begin : g_word_bit
            assign word_bit[w] = (cur_q ? bank1_q[w][LSB_FIRST ? bit_sel_q : LAST_BIT-bit_sel_q]
                                        : bank0_q[w][LSB_FIRST ? bit_sel_q : LAST_BIT-bit_sel_q]);
        end
    endgenerate

    always_comb begin
        serial_mux = 1'b0;
        for (int w = 0; w < WORDS; w++) begin
            if (word_sel_q == WORD_W'(w))
                serial_mux = word_bit[w];
        end
    end

    // Operand isolation: the selected bank's word mux is held at 0 while the
    // block is not emitting a valid bit.
    assign serial_out   = busy_q & serial_mux;
    assign serial_valid = busy_q;
    assign busy         = busy_q;
    assign frame_done   = frame_done_q;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            bank0_q      <= '0;
            bank1_q      <= '0;
            have_q       <= 2'b00;
            cur_q        <= 1'b0;
            busy_q       <= 1'b0;
            frame_done_q <= 1'b0;
            bit_sel_q    <= '0;
            word_sel_q   <= '0;
        end else begin
            frame_done_q <= 1'b0;

            if (wr_en) begin
                if (wr_bank)
                    bank1_q <= par_data;
                else
                    bank0_q <= par_data;
                have_q[wr_bank] <= 1'b1;
            end

            if (busy_q) begin
                if (enable) begin
                    if (word_sel_q == LAST_WORD && bit_sel_q == LAST_BIT) begin
                        busy_q       <= 1'b0;
                        have_q[cur_q] <= 1'b0;
                        frame_done_q <= 1'b1;
                        bit_sel_q    <= '0;
                        word_sel_q   <= '0;
                    end else if (bit_sel_q == LAST_BIT) begin
                        bit_sel_q  <= '0;
                        word_sel_q <= word_sel_q + 1'b1;
                    end else begin
                        bit_sel_q <= bit_sel_q + 1'b1;
                    end
                end
            end else if (start) begin
                // A frame is already captured in start_bank (>= 1 cycle old).
                cur_q      <= start_bank;
                busy_q     <= 1'b1;
                bit_sel_q  <= '0;
                word_sel_q <= '0;
            end
        end
    end

endmodule
