`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// PIPE-Lite elastic buffer with SKP insert/remove.
//
// Word format: {datak[1:0], data[15:0]}.  SKP is K28.5 on both bytes
// (datak=2'b11, data=16'hBCBC).
//
// A read tick consumes one word.  The buffer compensates a rate difference by
// inserting SKP when the fill is low and removing an SKP when the fill is high:
//
//   empty                  -> emit SKP, status 101 (underflow)
//   fill >= HIGH_WM, head is SKP -> drop the head, status 001
//   fill <= LOW_WM,  head not SKP -> emit SKP without consuming, status 001
//   otherwise              -> emit the head, status 000
//
// A write into a full buffer is dropped and latched as status 100 (overflow).
// -----------------------------------------------------------------------------
module pipe_elastic_buffer #(
    parameter int DEPTH    = 16,
    parameter int HIGH_WM  = 12,
    parameter int LOW_WM   = 4
) (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        wr_en,
    input  logic [17:0] wr_data,
    input  logic        rd_tick,
    output logic [17:0] rd_data,
    output logic        rd_valid,
    output logic [2:0]  status
);

    localparam int AW = $clog2(DEPTH);
    localparam logic [17:0] SKP_WORD = {2'b11, 16'hBCBC};

    logic [17:0] mem [0:DEPTH-1];
    logic [AW:0] wptr_q, rptr_q;
    logic [2:0]  status_q;
    logic        overflow_q;

    wire [AW:0] fill = wptr_q - rptr_q;
    wire        full = (fill == (AW+1)'(DEPTH));
    wire        empty = (fill == '0);
    wire [17:0] head = mem[rptr_q[AW-1:0]];
    wire        head_skp = (head == SKP_WORD);

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            wptr_q     <= '0;
            rptr_q     <= '0;
            status_q   <= 3'b000;
            overflow_q <= 1'b0;
        end else begin
            status_q <= 3'b000;
            if (wr_en) begin
                if (full) begin
                    overflow_q <= 1'b1;
                end else begin
                    mem[wptr_q[AW-1:0]] <= wr_data;
                    wptr_q <= wptr_q + 1'b1;
                end
            end
            if (overflow_q)
                status_q <= 3'b100;

            if (rd_tick) begin
                if (empty) begin
                    rd_data  <= SKP_WORD;
                    rd_valid <= 1'b1;
                    status_q <= 3'b101;
                end else if (fill >= (AW+1)'(HIGH_WM) && head_skp) begin
                    rptr_q   <= rptr_q + 1'b1;
                    rd_valid <= 1'b0;
                    status_q <= 3'b001;
                end else if (fill <= (AW+1)'(LOW_WM) && !head_skp) begin
                    rd_data  <= SKP_WORD;
                    rd_valid <= 1'b1;
                    status_q <= 3'b001;
                end else begin
                    rd_data  <= head;
                    rptr_q   <= rptr_q + 1'b1;
                    rd_valid <= 1'b1;
                    status_q <= 3'b000;
                end
            end else begin
                rd_valid <= 1'b0;
            end
        end
    end

    assign status = status_q;

endmodule
