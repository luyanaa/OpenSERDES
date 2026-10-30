`timescale 1ns/1ps
module tb_crc_tx_endpoint;
    logic clk=0,rst_n=0,en=0,clr=0,valid=0,ready,last=0;
    logic [7:0] data=0, sh=0;
    logic serial, serial_valid, busy, done, err;
    logic [7:0] expected[0:5];
    int bitcnt=0, errors=0;
    always #5 clk=~clk;

    serdes_tx_axis_digital #(
        .DATA_WIDTH(8), .WORDS(4), .LSB_FIRST(1'b1), .CRC_ENABLE(1'b1),
        .CRC_WIDTH(16), .CRC_POLY(16'h1021), .CRC_INIT(16'hFFFF)
    ) dut (
        .tx_clk(clk), .tx_rst_n(rst_n), .tx_enable(en), .tx_clear(clr),
        .s_axis_tvalid(valid), .s_axis_tready(ready), .s_axis_tdata(data),
        .s_axis_tlast(last), .tx_serial_out(serial), .tx_serial_valid(serial_valid),
        .tx_busy(busy), .tx_frame_done(done), .tx_frame_error(err)
    );

    always @(posedge clk) if (serial_valid) begin
        sh[bitcnt % 8] = serial;
        if (bitcnt % 8 == 7) begin
            if (sh !== expected[bitcnt/8]) begin
                $display("ERROR word %0d got %02h expected %02h", bitcnt/8, sh, expected[bitcnt/8]);
                errors++;
            end
        end
        bitcnt++;
    end

    task automatic send(input logic [7:0] x, input logic l);
        begin
            @(negedge clk); valid=1; data=x; last=l;
            while (!ready) @(negedge clk);
            @(posedge clk); #1; valid=0; last=0;
        end
    endtask

    initial begin
        expected[0]=8'h67; expected[1]=8'h45; expected[2]=8'h23; expected[3]=8'h01;
        expected[4]=8'hF6; expected[5]=8'h39;
        repeat (3) @(posedge clk); rst_n=1; repeat (3) @(posedge clk); en=1;
        send(8'h67,0); send(8'h45,0); send(8'h23,0); send(8'h01,1);
        repeat (120) @(posedge clk);
        if (bitcnt != 48) begin $display("ERROR bits=%0d", bitcnt); errors++; end
        if (err) begin $display("ERROR frame_error"); errors++; end
        if (errors) $display("FAIL crc tx endpoint errors=%0d", errors);
        else $display("PASS crc tx endpoint 4 payload + CRC16");
        $finish;
    end
    initial begin #20000; $display("FAIL timeout"); $finish; end
endmodule
