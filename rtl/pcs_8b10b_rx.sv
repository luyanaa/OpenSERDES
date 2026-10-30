`timescale 1ns/1ps

// 8b/10b receive PCS wrapper.
//
// A valid 10-bit code word produces a byte/control indication one cycle later.
// Code and disparity errors are reported with the decoded byte so the link
// scoreboard can distinguish bad symbols from lost alignment.
module pcs_8b10b_rx (
    input  logic       clk,
    input  logic       rst_n,
    input  logic       enable,
    input  logic       valid,
    input  logic [9:0] code,
    output logic [7:0] data,
    output logic       ctrl,
    output logic       data_valid,
    output logic       code_error,
    output logic       disp_error
);
    logic [7:0] data_q;
    logic       ctrl_q;
    logic       code_err_q;
    logic       disp_err_q;
    logic       valid_q;

    decoder_8b10b u_dec (
        .i_clk(clk), .i_reset_n(rst_n), .i_en(enable && valid),
        .i_10b(code), .o_8b(data_q), .o_ctrl(ctrl_q),
        .o_code_err(code_err_q), .o_disp_err(disp_err_q)
    );

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            valid_q <= 1'b0;
        else
            valid_q <= enable && valid;
    end

    assign data       = data_q;
    assign ctrl       = ctrl_q;
    assign data_valid = valid_q;
    assign code_error = code_err_q;
    assign disp_error = disp_err_q;
endmodule
