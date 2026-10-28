`timescale 1ns/1ps

// AXI4-Stream slave -> serializer_stream word handshake.
//
// One AXIS beat is one DATA_WIDTH word.  A frame is exactly WORDS beats and
// tlast must be asserted on the last beat.  Violations set frame_error sticky;
// clear is a synchronous clear (e.g. from an APB CSR).
module axis_tx_adapter #(
    parameter int DATA_WIDTH = 32,
    parameter int WORDS      = 8
) (
    input  logic                  clk,
    input  logic                  rst_n,
    input  logic                  clear,
    input  logic                  s_axis_tvalid,
    output logic                  s_axis_tready,
    input  logic [DATA_WIDTH-1:0] s_axis_tdata,
    input  logic                  s_axis_tlast,
    output logic                  in_valid,
    input  logic                  in_ready,
    output logic [DATA_WIDTH-1:0] in_word,
    output logic                  frame_error
);

    localparam int WORD_W = (WORDS <= 1) ? 1 : $clog2(WORDS);
    localparam logic [WORD_W-1:0] LAST_WORD = WORD_W'(WORDS-1);

    logic [WORD_W-1:0] beat_q;
    logic              error_q;

    assign s_axis_tready = in_ready;
    assign in_valid      = s_axis_tvalid;
    assign in_word       = s_axis_tdata;
    assign frame_error   = error_q;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            beat_q  <= '0;
            error_q <= 1'b0;
        end else if (clear) begin
            beat_q  <= '0;
            error_q <= 1'b0;
        end else if (s_axis_tvalid && s_axis_tready) begin
            if (beat_q == LAST_WORD) begin
                if (!s_axis_tlast)
                    error_q <= 1'b1;
                beat_q <= '0;
            end else begin
                if (s_axis_tlast)
                    error_q <= 1'b1;
                beat_q <= beat_q + 1'b1;
            end
        end
    end

endmodule
