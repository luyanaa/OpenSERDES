`timescale 1ns/1ps

module tb_channel;

    logic clk;
    logic rst_n;
    logic din;
    logic dval;

    logic dout_d2, dval_d2;
    logic dout_inv, dval_inv;
    logic dout_err, dval_err;

    int errors;

    channel_model #(.DELAY_CYCLES(2), .BIT_ERROR_PERIOD(0), .INVERT(1'b0)) ch_d2 (
        .clk(clk), .rst_n(rst_n), .serial_in(din), .serial_valid(dval),
        .serial_out(dout_d2), .serial_valid_out(dval_d2)
    );

    channel_model #(.DELAY_CYCLES(0), .BIT_ERROR_PERIOD(0), .INVERT(1'b1)) ch_inv (
        .clk(clk), .rst_n(rst_n), .serial_in(din), .serial_valid(dval),
        .serial_out(dout_inv), .serial_valid_out(dval_inv)
    );

    channel_model #(.DELAY_CYCLES(0), .BIT_ERROR_PERIOD(4), .INVERT(1'b0)) ch_err (
        .clk(clk), .rst_n(rst_n), .serial_in(din), .serial_valid(dval),
        .serial_out(dout_err), .serial_valid_out(dval_err)
    );

    always #5 clk = ~clk;

    initial begin
        logic [15:0] pattern;
        logic exp_err;

        clk    = 1'b0;
        rst_n  = 1'b0;
        din    = 1'b0;
        dval   = 1'b0;
        errors = 0;
        pattern = 16'b1011_0010_1101_0101;

        repeat (3) @(posedge clk);
        rst_n = 1'b1;
        repeat (2) @(posedge clk);

        for (int i = 0; i < 16; i++) begin
            @(negedge clk);
            din  = pattern[i];
            dval = 1'b1;

            @(posedge clk);
            #1;
            if (i >= 1 && dval_d2 && dout_d2 !== pattern[i-1]) begin
                $display("ERROR: delay channel bit %0d got %0b expected %0b",
                         i, dout_d2, pattern[i-1]);
                errors++;
            end

            if (dval_inv && dout_inv !== ~pattern[i]) begin
                $display("ERROR: invert channel bit %0d got %0b expected %0b",
                         i, dout_inv, ~pattern[i]);
                errors++;
            end

            exp_err = pattern[i] ^ ((i % 4) == 3);
            if (dval_err && dout_err !== exp_err) begin
                $display("ERROR: error channel bit %0d got %0b expected %0b",
                         i, dout_err, exp_err);
                errors++;
            end
        end

        dval = 1'b0;
        repeat (4) @(posedge clk);
        if (errors != 0) begin
            $display("FAIL: tb_channel errors=%0d", errors);
            $fatal(1);
        end
        $display("PASS: tb_channel");
        $finish;
    end

endmodule
