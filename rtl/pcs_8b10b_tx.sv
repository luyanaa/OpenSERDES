`timescale 1ns/1ps

// 8b/10b transmit PCS wrapper.
//
// One valid input byte produces one 10-bit code word.  The underlying libsv
// encoder maintains running disparity across frames; `enable` holds the
// current disparity state when low.  This wrapper does not add commas or PCS
// framing; those remain the link layer's responsibility.
module pcs_8b10b_tx (
    input  logic       clk,
    input  logic       rst_n,
    input  logic       enable,
    input  logic       valid,
    input  logic [7:0] data,
    input  logic       ctrl,
    output logic [9:0] code,
    output logic       code_valid,
    output logic       code_error
);
    logic [9:0] code_q;
    logic       err_q;
    logic       valid_q;

    encoder_8b10b u_enc (
        .i_clk(clk), .i_reset_n(rst_n), .i_en(enable && valid),
        .i_8b(data), .i_ctrl(ctrl), .o_10b(code_q), .o_code_err(err_q)
    );

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            valid_q <= 1'b0;
        else
            valid_q <= enable && valid;
    end

    assign code       = code_q;
    assign code_valid = valid_q;
    assign code_error = err_q;
endmodule
