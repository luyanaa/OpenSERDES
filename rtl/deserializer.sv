`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// Abstract, technology-independent deserializer.
//
// Frame format:
//   - RATIO = WORDS * DATA_WIDTH serial bits form one frame.
//   - Bits are written into the selected word's register bank.
//   - LSB_FIRST=1: first serial bit is word0 bit0.
//   - LSB_FIRST=0: first serial bit is word0 bit31.
//
// Handshake:
//   - serial_valid marks a valid serial bit; enable gates acceptance.
//   - busy is high while a frame is being captured.
//   - par_valid pulses for one clock when par_data has been updated.
//
// Implementation notes:
//   - par_data is a packed array: [WORDS-1:0][DATA_WIDTH-1:0].
//   - One deserializer_word instance per word is created by a generate loop.
//   - There is no INT_PAR_IN shadow register and no COMPLETE-derived clock.
//   - The captured word data is the output register bank itself.
//   - Word select and bit select are one-hot shift registers.  The old binary
//     comparator sat in the 400 MHz sample-clock path; one-hot removes it from
//     every word's capture enable.
// -----------------------------------------------------------------------------
module deserializer #(
    parameter int DATA_WIDTH = 32,
    parameter int WORDS      = 8,
    parameter bit LSB_FIRST  = 1'b1
) (
    input  logic                              clk,
    input  logic                              rst_n,
    input  logic                              enable,
    input  logic                              align,
    input  logic                              flush,
    input  logic                              serial_in,
    input  logic                              serial_valid,
    output logic [WORDS-1:0][DATA_WIDTH-1:0]  par_data,
    output logic                              par_valid,
    output logic                              busy
);

    localparam int LAST_BIT_IDX  = DATA_WIDTH - 1;
    localparam int LAST_WORD_IDX = WORDS - 1;
    localparam logic [DATA_WIDTH-1:0] ONE_HOT_BIT_ZERO =
        {{(DATA_WIDTH - 1){1'b0}}, 1'b1};
    localparam logic [WORDS-1:0] ONE_HOT_WORD_ZERO =
        {{(WORDS - 1){1'b0}}, 1'b1};

    logic [WORDS-1:0][DATA_WIDTH-1:0] word_data;
    logic [DATA_WIDTH-1:0]            bit_sel_onehot_q;
    logic [WORDS-1:0]                 word_sel_onehot_q;
    logic                             busy_q;
    logic                             par_valid_q;
    logic                             capture;

    assign par_data  = word_data;
    assign par_valid = par_valid_q;
    assign busy      = busy_q;
    assign capture   = serial_valid & enable;

    // One capture bank per word.  Only the selected word is written, and the
    // bit select is a one-hot enable rather than a binary comparator.
    generate
        for (genvar w = 0; w < WORDS; w++) begin : g_word
            deserializer_word #(
                .DATA_WIDTH(DATA_WIDTH),
                .LSB_FIRST(LSB_FIRST)
            ) u_word (
                .clk(clk),
                .rst_n(rst_n),
                .capture(capture && word_sel_onehot_q[w]),
                .serial_in(serial_in),
                .bit_sel_onehot(bit_sel_onehot_q),
                .data(word_data[w])
            );
        end
    endgenerate

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            bit_sel_onehot_q  <= ONE_HOT_BIT_ZERO;
            word_sel_onehot_q <= ONE_HOT_WORD_ZERO;
            busy_q            <= 1'b0;
            par_valid_q       <= 1'b0;
        end else begin
            par_valid_q <= 1'b0;

            if (align || flush) begin
                // Frame alignment/flush: the next valid bit becomes bit 0.
                bit_sel_onehot_q  <= ONE_HOT_BIT_ZERO;
                word_sel_onehot_q <= ONE_HOT_WORD_ZERO;
                busy_q            <= 1'b0;
                par_valid_q       <= 1'b0;
            end else if (capture) begin
                if (bit_sel_onehot_q[LAST_BIT_IDX]) begin
                    bit_sel_onehot_q <= ONE_HOT_BIT_ZERO;

                    if (word_sel_onehot_q[LAST_WORD_IDX]) begin
                        word_sel_onehot_q <= ONE_HOT_WORD_ZERO;
                        busy_q            <= 1'b0;
                        par_valid_q       <= 1'b1;
                    end else begin
                        word_sel_onehot_q <= word_sel_onehot_q << 1;
                        busy_q            <= 1'b1;
                    end
                end else begin
                    bit_sel_onehot_q <= bit_sel_onehot_q << 1;
                    busy_q           <= 1'b1;
                end
            end
        end
    end

endmodule
