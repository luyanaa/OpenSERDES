`timescale 1ns/1ps

// Unit test for the full-rate sampler -> word-clock pair boundary.
module tb_receiver_pair_sampler;

    logic clk_bit;
    logic clk_word;
    logic rst_n;
    logic enable;
    logic serial_in;
    logic pair_valid;
    logic bit0;
    logic bit1;

    logic model_sr0;
    logic model_sr1;
    int errors;

    always #2.5 clk_bit = ~clk_bit;
    always @(posedge clk_bit) clk_word <= ~clk_word;

    receiver_pair_sampler dut (
        .clk_bit(clk_bit), .clk_word(clk_word), .rst_n(rst_n),
        .enable(enable), .serial_in(serial_in),
        .pair_valid(pair_valid), .bit0(bit0), .bit1(bit1)
    );

    always_ff @(posedge clk_bit or negedge rst_n) begin
        if (!rst_n) begin
            model_sr0 <= 1'b0;
            model_sr1 <= 1'b0;
        end else if (enable) begin
            model_sr0 <= serial_in;
            model_sr1 <= model_sr0;
        end
    end

    always @(posedge clk_word) begin
        if (rst_n) begin
            #1;
            if (pair_valid !== enable) begin
                $display("ERROR: pair_valid=%b expected=%b", pair_valid, enable);
                errors++;
            end
            if (bit0 !== model_sr1 || bit1 !== model_sr0) begin
                $display("ERROR: pair got %b%b expected %b%b",
                         bit0, bit1, model_sr1, model_sr0);
                errors++;
            end
        end
    end

    initial begin
        clk_bit      = 1'b0;
        clk_word     = 1'b0;
        rst_n        = 1'b0;
        enable       = 1'b0;
        serial_in    = 1'b0;
        errors       = 0;
        repeat (3) @(posedge clk_bit);
        rst_n = 1'b1;
        repeat (2) @(posedge clk_bit);
        enable = 1'b1;

        for (int i = 0; i < 200; i++) begin
            @(negedge clk_bit);
            serial_in = $urandom;
        end
        repeat (4) @(posedge clk_bit);
        #1;
        if (errors != 0) begin
            $display("FAIL: tb_receiver_pair_sampler errors=%0d", errors);
            $fatal(1);
        end
        $display("PASS: tb_receiver_pair_sampler");
        $finish;
    end

endmodule
