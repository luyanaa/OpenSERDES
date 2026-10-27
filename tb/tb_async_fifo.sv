`timescale 1ns/1ps

// Dual-clock FIFO test: ordering, full/empty, and reset.
module tb_async_fifo;

    localparam int DW    = 16;
    localparam int DEPTH = 16;

    logic wclk;
    logic wrst_n;
    logic winc;
    logic [DW-1:0] wdata;
    logic wfull;

    logic rclk;
    logic rrst_n;
    logic rinc;
    logic [DW-1:0] rdata;
    logic rempty;

    int errors;

    always #5 wclk = ~wclk;
    always #7 rclk = ~rclk;

    async_fifo #(.DATA_WIDTH(DW), .DEPTH(DEPTH), .SYNC_STAGES(2)) dut (
        .wclk(wclk), .wrst_n(wrst_n), .winc(winc), .wdata(wdata), .wfull(wfull),
        .rclk(rclk), .rrst_n(rrst_n), .rinc(rinc), .rdata(rdata), .rempty(rempty)
    );

    task automatic fifo_write(input logic [DW-1:0] data);
        begin
            @(negedge wclk);
            while (wfull) @(negedge wclk);
            winc  = 1'b1;
            wdata = data;
            @(posedge wclk);
            #1;
            winc = 1'b0;
        end
    endtask

    task automatic fifo_read(output logic [DW-1:0] data);
        begin
            @(negedge rclk);
            while (rempty) @(negedge rclk);
            rinc = 1'b1;
            #1;
            data = rdata;
            @(posedge rclk);
            #1;
            rinc = 1'b0;
        end
    endtask

    initial begin
        logic [DW-1:0] value;
        wclk   = 1'b0;
        rclk   = 1'b0;
        wrst_n = 1'b0;
        rrst_n = 1'b0;
        winc   = 1'b0;
        rinc   = 1'b0;
        wdata  = '0;
        errors = 0;

        repeat (3) @(posedge wclk);
        repeat (3) @(posedge rclk);
        wrst_n = 1'b1;
        rrst_n = 1'b1;
        repeat (2) @(posedge wclk);
        repeat (2) @(posedge rclk);

        for (int i = 0; i < 8; i++)
            fifo_write(16'h1000 + i);
        for (int i = 0; i < 8; i++) begin
            fifo_read(value);
            if (value !== (16'h1000 + i)) begin
                $display("ERROR: got %h expected %h", value, 16'h1000 + i);
                errors++;
            end
        end

        for (int i = 0; i < DEPTH; i++)
            fifo_write(16'h2000 + i);
        if (!wfull) begin
            $display("ERROR: FIFO not full after DEPTH writes");
            errors++;
        end
        for (int i = 0; i < DEPTH; i++) begin
            fifo_read(value);
            if (value !== (16'h2000 + i)) begin
                $display("ERROR: full-run got %h expected %h",
                         value, 16'h2000 + i);
                errors++;
            end
        end
        if (!rempty) begin
            $display("ERROR: FIFO not empty after drain");
            errors++;
        end

        if (errors != 0) begin
            $display("FAIL: tb_async_fifo errors=%0d", errors);
            $fatal(1);
        end
        $display("PASS: tb_async_fifo");
        $finish;
    end

endmodule
