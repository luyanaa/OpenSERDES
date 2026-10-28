`timescale 1ns/1ps

module tb_axis_tx_adapter;

    localparam int DW    = 16;
    localparam int WORDS = 4;

    logic clk;
    logic rst_n;
    logic clear;
    logic s_axis_tvalid;
    logic s_axis_tready;
    logic [DW-1:0] s_axis_tdata;
    logic s_axis_tlast;
    logic in_valid;
    logic in_ready;
    logic [DW-1:0] in_word;
    logic frame_error;

    int errors;

    always #5 clk = ~clk;

    axis_tx_adapter #(.DATA_WIDTH(DW), .WORDS(WORDS)) dut (
        .clk(clk), .rst_n(rst_n), .clear(clear),
        .s_axis_tvalid(s_axis_tvalid), .s_axis_tready(s_axis_tready),
        .s_axis_tdata(s_axis_tdata), .s_axis_tlast(s_axis_tlast),
        .in_valid(in_valid), .in_ready(in_ready), .in_word(in_word),
        .frame_error(frame_error)
    );

    task automatic send_word(input logic [DW-1:0] data, input logic last);
        begin
            @(negedge clk);
            s_axis_tvalid = 1'b1;
            s_axis_tdata  = data;
            s_axis_tlast  = last;
            while (!s_axis_tready) @(negedge clk);
            if (!in_valid || in_word !== data) begin
                $display("ERROR: adapter did not forward word %h", data);
                errors++;
            end
            @(posedge clk);
            #1;
            s_axis_tvalid = 1'b0;
            s_axis_tlast  = 1'b0;
        end
    endtask

    initial begin
        clk           = 1'b0;
        rst_n         = 1'b0;
        clear         = 1'b0;
        s_axis_tvalid = 1'b0;
        s_axis_tdata  = '0;
        s_axis_tlast  = 1'b0;
        in_ready      = 1'b1;
        errors        = 0;

        repeat (3) @(posedge clk);
        rst_n = 1'b1;
        repeat (2) @(posedge clk);

        for (int i = 0; i < WORDS; i++)
            send_word(16'h1000 + i, (i == WORDS-1));
        if (frame_error) begin
            $display("ERROR: valid frame set frame_error");
            errors++;
        end

        // Early tlast.
        send_word(16'h2000, 1'b1);
        if (!frame_error) begin
            $display("ERROR: early tlast not detected");
            errors++;
        end
        @(negedge clk); clear = 1'b1; @(posedge clk); #1; clear = 1'b0;
        if (frame_error) begin
            $display("ERROR: clear did not clear frame_error");
            errors++;
        end

        // Missing tlast.
        for (int i = 0; i < WORDS; i++)
            send_word(16'h3000 + i, 1'b0);
        if (!frame_error) begin
            $display("ERROR: missing tlast not detected");
            errors++;
        end

        if (errors != 0) begin
            $display("FAIL: tb_axis_tx_adapter errors=%0d", errors);
            $fatal(1);
        end
        $display("PASS: tb_axis_tx_adapter");
        $finish;
    end

endmodule
