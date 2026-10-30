`timescale 1ns/1ps

module tb_crc_frame;
    logic clk = 1'b0;
    logic rst_n = 1'b0;
    logic enable = 1'b1;
    logic start;
    logic last;
    logic valid;
    logic [7:0] data;
    logic [15:0] crc16;
    logic [15:0] crc16_raw;
    logic crc16_valid;
    logic [31:0] crc32;
    logic [31:0] crc32_raw;
    logic crc32_valid;

    always #5 clk = ~clk;

    crc_frame #(
        .DATA_WIDTH(8), .CRC_WIDTH(16), .POLY(16'h1021),
        .INIT(16'hFFFF), .LSB_FIRST(1'b0),
        .REFLECT_OUT(1'b0), .XOR_OUT(16'h0000)
    ) u_crc16 (
        .clk(clk), .rst_n(rst_n), .enable(enable),
        .frame_start(start), .frame_end(last), .data_valid(valid),
        .data(data), .crc(crc16), .crc_raw(crc16_raw),
        .crc_valid(crc16_valid)
    );

    crc_frame #(
        .DATA_WIDTH(8), .CRC_WIDTH(32), .POLY(32'h04C11DB7),
        .INIT(32'hFFFFFFFF), .LSB_FIRST(1'b1),
        .REFLECT_OUT(1'b1), .XOR_OUT(32'hFFFFFFFF)
    ) u_crc32 (
        .clk(clk), .rst_n(rst_n), .enable(enable),
        .frame_start(start), .frame_end(last), .data_valid(valid),
        .data(data), .crc(crc32), .crc_raw(crc32_raw),
        .crc_valid(crc32_valid)
    );

    task automatic send_byte(input logic [7:0] value, input logic is_last);
        begin
            @(negedge clk);
            data = value;
            valid = 1'b1;
            last = is_last;
            @(negedge clk);
            valid = 1'b0;
            last = 1'b0;
        end
    endtask

    initial begin
        start = 1'b0; last = 1'b0; valid = 1'b0; data = '0;
        repeat (3) @(posedge clk);
        rst_n = 1'b1;
        @(negedge clk);
        start = 1'b1;
        @(negedge clk);
        start = 1'b0;

        send_byte(8'h31, 1'b0);
        send_byte(8'h32, 1'b0);
        send_byte(8'h33, 1'b0);
        send_byte(8'h34, 1'b0);
        send_byte(8'h35, 1'b0);
        send_byte(8'h36, 1'b0);
        send_byte(8'h37, 1'b0);
        send_byte(8'h38, 1'b0);
        send_byte(8'h39, 1'b1);

        @(posedge clk);
        if (crc16 !== 16'h29B1) begin
            $display("FAIL CRC16 got %04h expected 29B1", crc16);
            $finish;
        end
        if (crc32 !== 32'hCBF43926) begin
            $display("FAIL CRC32 got %08h expected CBF43926", crc32);
            $finish;
        end
        $display("PASS CRC16=%04h CRC32=%08h", crc16, crc32);
        $finish;
    end

    initial begin
        #2000;
        $display("FAIL timeout");
        $finish;
    end

endmodule
