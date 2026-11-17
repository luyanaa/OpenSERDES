`timescale 1ns/1ps

// Elastic buffer SKP insert/remove: overflow, underflow, high-watermark drop
// and low-watermark insert.
module tb_pipe_elastic_buffer;
    logic clk, rst_n;
    logic wr_en, rd_tick;
    logic [17:0] wr_data, rd_data;
    logic rd_valid;
    logic [2:0] status;

    int errors;
    localparam logic [17:0] SKP = {2'b11, 16'hBCBC};

    always #5 clk = ~clk;

    pipe_elastic_buffer #(.DEPTH(16), .HIGH_WM(12), .LOW_WM(4)) dut (
        .clk(clk), .rst_n(rst_n),
        .wr_en(wr_en), .wr_data(wr_data),
        .rd_tick(rd_tick), .rd_data(rd_data),
        .rd_valid(rd_valid), .status(status)
    );

    task automatic wr(input logic [15:0] d);
        begin
            @(negedge clk);
            wr_en = 1'b1;
            wr_data = {2'b00, d};
            @(negedge clk);
            wr_en = 1'b0;
        end
    endtask

    task automatic tick;
        begin
            @(negedge clk);
            rd_tick = 1'b1;
            @(negedge clk);
            rd_tick = 1'b0;
            #1;
        end
    endtask

    initial begin
        clk = 0; rst_n = 0;
        wr_en = 0; wr_data = '0; rd_tick = 0;
        errors = 0;

        repeat (3) @(posedge clk);
        rst_n = 1;

        // Underflow: read with no writes.
        tick();
        if (!rd_valid || rd_data !== SKP || status !== 3'b101) begin
            $display("FAIL: underflow got valid=%b data=%h status=%b",
                     rd_valid, rd_data, status);
            errors++;
        end

        // Fill 14 words (above HIGH_WM), then drop an SKP at the head.
        for (int i = 0; i < 14; i++) wr(16'h0100 + i);
        // Put an SKP at the head: write it first? The head is 0x0100.
        // Instead, fill to the high watermark and push an SKP as the next
        // head by draining until the SKP arrives is complex; test the drop
        // path directly by writing an SKP and draining to it.
        // Simpler: reset, write SKP + 13 words -> fill 14, head is SKP.
        rst_n = 0;
        repeat (2) @(posedge clk);
        rst_n = 1;
        wr_en = 1; wr_data = SKP;
        @(negedge clk); wr_en = 0;
        for (int i = 0; i < 13; i++) wr(16'h0200 + i);
        tick();
        if (rd_valid || status !== 3'b001) begin
            $display("FAIL: high-watermark SKP drop valid=%b status=%b",
                     rd_valid, status);
            errors++;
        end

        // Low watermark insert: keep the fill above LOW_WM, drain to the
        // watermark, then check that SKP is inserted without consuming the
        // head; raising the fill again must release the head.
        rst_n = 0;
        repeat (2) @(posedge clk);
        rst_n = 1;
        for (int i = 0; i < 6; i++) wr(16'h0300 + i);
        tick();
        if (!rd_valid || rd_data !== {2'b00, 16'h0300}) begin
            $display("FAIL: first drain data=%h", rd_data);
            errors++;
        end
        tick();
        if (!rd_valid || rd_data !== {2'b00, 16'h0301}) begin
            $display("FAIL: second drain data=%h", rd_data);
            errors++;
        end
        tick();   // fill 4 <= LOW_WM, head 0x0302 not SKP -> insert
        if (!rd_valid || rd_data !== SKP || status !== 3'b001) begin
            $display("FAIL: low-watermark insert valid=%b data=%h status=%b",
                     rd_valid, rd_data, status);
            errors++;
        end
        for (int i = 0; i < 2; i++) wr(16'h0310 + i);
        tick();
        if (!rd_valid || rd_data !== {2'b00, 16'h0302}) begin
            $display("FAIL: word lost after insert data=%h", rd_data);
            errors++;
        end

        // Overflow: write DEPTH+1 words without reading.
        rst_n = 0;
        repeat (2) @(posedge clk);
        rst_n = 1;
        for (int i = 0; i < 17; i++) wr(16'h0400 + i);
        @(posedge clk); #1;
        if (status !== 3'b100) begin
            $display("FAIL: overflow status=%b", status);
            errors++;
        end

        if (errors == 0)
            $display("PASS: tb_pipe_elastic_buffer");
        else
            $display("FAIL: tb_pipe_elastic_buffer errors=%0d", errors);
        $finish;
    end

    initial begin
        #100000;
        $display("FAIL: timeout");
        $finish;
    end
endmodule
