`timescale 1ns/1ps

module tb_axis_crc;
    logic clk = 0, rst_n = 0, enable = 1;
    logic s_valid = 0, s_last = 0, s_ready;
    logic [7:0] s_data = 0;
    logic ins_valid, ins_ready, ins_last, ins_user;
    logic [7:0] ins_data;
    logic [15:0] crc_word;
    logic chk_valid, chk_ready = 1, chk_last;
    logic [7:0] chk_data;
    logic crc_error, crc_ok;
    int beats = 0;
    logic saw_ok = 0, saw_error = 0;

    always #5 clk = ~clk;

    axis_crc_inserter #(
        .DATA_WIDTH(8), .CRC_WIDTH(16), .POLY(16'h1021),
        .INIT(16'hFFFF), .LSB_FIRST(0), .REFLECT_OUT(0), .XOR_OUT(0)
    ) u_ins (
        .clk(clk), .rst_n(rst_n), .enable(enable),
        .s_axis_tvalid(s_valid), .s_axis_tready(s_ready),
        .s_axis_tdata(s_data), .s_axis_tlast(s_last),
        .m_axis_tvalid(ins_valid), .m_axis_tready(ins_ready),
        .m_axis_tdata(ins_data), .m_axis_tlast(ins_last),
        .m_axis_tuser(ins_user), .crc_word(crc_word)
    );

    axis_crc_checker #(
        .DATA_WIDTH(8), .CRC_WIDTH(16), .POLY(16'h1021),
        .INIT(16'hFFFF), .LSB_FIRST(0), .REFLECT_OUT(0), .XOR_OUT(0)
    ) u_chk (
        .clk(clk), .rst_n(rst_n), .enable(enable),
        .s_axis_tvalid(ins_valid), .s_axis_tready(ins_ready),
        .s_axis_tdata(ins_data), .s_axis_tlast(ins_last),
        .s_axis_tuser(ins_user), .m_axis_tvalid(chk_valid), .m_axis_tready(chk_ready),
        .m_axis_tdata(chk_data), .m_axis_tlast(chk_last),
        .crc_error(crc_error), .crc_ok(crc_ok)
    );

    always_ff @(posedge clk) begin
        if (chk_valid && chk_ready)
            beats <= beats + 1;
        if (crc_ok)
            saw_ok <= 1'b1;
        if (crc_error)
            saw_error <= 1'b1;
    end

    task automatic send_byte(input logic [7:0] v, input logic last);
        begin
            @(negedge clk);
            s_valid = 1'b1;
            s_data = v;
            s_last = last;
            while (!s_ready) @(negedge clk);
            @(negedge clk);
            s_valid = 1'b0;
            s_last = 1'b0;
        end
    endtask

    initial begin
        repeat (3) @(posedge clk);
        rst_n = 1'b1;
        repeat (2) @(posedge clk);
        send_byte(8'h31, 0); send_byte(8'h32, 0); send_byte(8'h33, 0);
        send_byte(8'h34, 0); send_byte(8'h35, 0); send_byte(8'h36, 0);
        send_byte(8'h37, 0); send_byte(8'h38, 0); send_byte(8'h39, 1);
        repeat (20) @(posedge clk);
        if (crc_word !== 16'h29B1) begin
            $display("FAIL crc_word=%04h", crc_word); $finish;
        end
        if (beats != 9) begin
            $display("FAIL payload beats=%0d", beats); $finish;
        end
        if (!saw_ok || saw_error) begin
            $display("FAIL crc_ok=%0d error=%0d", saw_ok, saw_error); $finish;
        end
        $display("PASS AXIS CRC payload_beats=%0d crc=%04h", beats, crc_word);
        $finish;
    end

    initial begin
        #3000; $display("FAIL timeout"); $finish;
    end
endmodule
