`timescale 1ns/1ps
module tb_crc_rx_endpoint;
    logic clk=0,rst_n=0,en=0,flush=0,align=0,serial_in=0,valid,ready=0,last,crc_error;
    logic [7:0] data;
    logic lock,stale,frame_locked,ov,par,ss,sd,sv;
    logic signed [12:0] ft;
    logic busy,cout;
    logic [7:0] bytes[0:5];
    int errors=0;
    always #5 clk=~clk;

    serdes_rx_axis_digital #(
        .DATA_WIDTH(8), .WORDS(4), .LSB_FIRST(1'b1),
        .OVERSAMPLE(1), .CDR_BYPASS(1'b1), .ALIGN_MODE(0),
        .CRC_ENABLE(1'b1), .CRC_WIDTH(16), .CRC_POLY(16'h1021),
        .CRC_INIT(16'hFFFF)
    ) dut (
        .rx_clk(clk), .rx_rst_n(rst_n), .rx_enable(en), .rx_flush(flush),
        .rx_align(align), .rx_serial_in(serial_in), .rx_clear(1'b0),
        .phase_offset('0),
        .m_axis_tvalid(valid), .m_axis_tready(ready), .m_axis_tdata(data),
        .m_axis_tlast(last), .rx_lock(lock), .rx_lock_stale(stale),
        .rx_frame_locked(frame_locked), .rx_frame_overflow(ov),
        .rx_par_valid(par), .rx_sample_strobe(ss), .rx_serial_data(sd),
        .rx_serial_valid(sv), .rx_freq_trim(ft), .rx_busy(busy),
        .rx_clk_out(cout), .crc_error(crc_error)
    );

    task automatic read_beat(output logic [7:0] d, output logic l);
        begin
            @(negedge clk);
            while (!valid) @(negedge clk);
            d=data; l=last; ready=1;
            @(posedge clk); #1; ready=0;
        end
    endtask

    initial begin
        logic [7:0] d;
        logic l;
        bytes[0]=8'h67; bytes[1]=8'h45; bytes[2]=8'h23; bytes[3]=8'h01;
        bytes[4]=8'hF6; bytes[5]=8'h39;
        repeat (3) @(posedge clk); rst_n=1; repeat (3) @(posedge clk);
        @(negedge clk); en=1;
        for (int i=0; i<48; i++) begin
            serial_in = bytes[i/8][i%8];
            @(posedge clk); #1;
        end
        @(posedge clk); #1;
        for (int w=0; w<4; w++) begin
            read_beat(d,l);
            if (d !== bytes[w] || l !== (w==3)) begin
                $display("ERROR word=%0d got=%02h last=%0d expected=%02h",
                         w, d, l, bytes[w]);
                errors++;
            end
        end
        repeat (10) @(posedge clk);
        if (crc_error) begin $display("ERROR crc_error"); errors++; end
        if (errors) $display("FAIL crc rx endpoint errors=%0d", errors);
        else $display("PASS crc rx endpoint 4 payload + CRC16");
        $finish;
    end
    initial begin #20000; $display("FAIL timeout"); $finish; end
endmodule
