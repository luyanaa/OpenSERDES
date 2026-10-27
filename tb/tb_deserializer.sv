`timescale 1ns/1ps

`ifndef TB_LSB_FIRST
`define TB_LSB_FIRST 1
`endif

module tb_deserializer #(
    parameter int DATA_WIDTH = 8,
    parameter int WORDS      = 4,
    parameter bit LSB_FIRST  = (`TB_LSB_FIRST != 0)
);

    localparam int RATIO = DATA_WIDTH * WORDS;

    logic clk;
    logic rst_n;
    logic enable;
    logic serial_in;
    logic serial_valid;
    logic [WORDS-1:0][DATA_WIDTH-1:0] par_data;
    logic par_valid;
    logic busy;

    int errors;

    deserializer #(
        .DATA_WIDTH(DATA_WIDTH),
        .WORDS(WORDS),
        .LSB_FIRST(LSB_FIRST)
    ) dut (
        .clk(clk),
        .rst_n(rst_n),
        .enable(enable),
        .align(1'b0),
        .flush(1'b0),
        .serial_in(serial_in),
        .serial_valid(serial_valid),
        .par_data(par_data),
        .par_valid(par_valid),
        .busy(busy)
    );

    always #5 clk = ~clk;

    function automatic logic expected_bit(input logic [RATIO-1:0] data, input int unsigned i);
        int unsigned word_i;
        int unsigned bit_i;
        begin
            word_i = i / DATA_WIDTH;
            bit_i  = i % DATA_WIDTH;
            if (LSB_FIRST)
                expected_bit = data[word_i*DATA_WIDTH + bit_i];
            else
                expected_bit = data[word_i*DATA_WIDTH + (DATA_WIDTH-1-bit_i)];
        end
    endfunction

    task automatic check_frame(input logic [RATIO-1:0] data);
        begin
            @(negedge clk);
            if (busy) begin
                $display("ERROR: busy before frame");
                errors++;
            end

            for (int i = 0; i < RATIO; i++) begin
                serial_in    = expected_bit(data, i);
                serial_valid = 1'b1;
                enable       = 1'b1;
                @(posedge clk);
                #1;
                if (i == 0 && !busy) begin
                    $display("ERROR: busy did not assert after first bit");
                    errors++;
                end
            end

            if (!par_valid) begin
                $display("ERROR: par_valid missing");
                errors++;
            end
            if (busy) begin
                $display("ERROR: busy still high after frame");
                errors++;
            end
            for (int w = 0; w < WORDS; w++) begin
                if (par_data[w] !== data[w*DATA_WIDTH +: DATA_WIDTH]) begin
                    $display("ERROR: par_data[%0d]=%h expected=%h",
                             w, par_data[w], data[w*DATA_WIDTH +: DATA_WIDTH]);
                    errors++;
                end
            end

            serial_valid = 1'b0;
            enable       = 1'b0;
            @(posedge clk);
            #1;
            if (par_valid) begin
                $display("ERROR: par_valid did not deassert");
                errors++;
            end
        end
    endtask

    initial begin
        clk          = 1'b0;
        rst_n        = 1'b0;
        enable       = 1'b0;
        serial_in    = 1'b0;
        serial_valid = 1'b0;
        errors       = 0;

        repeat (3) @(posedge clk);
        rst_n = 1'b1;
        repeat (2) @(posedge clk);

        check_frame('0);
        check_frame('1);
        check_frame({RATIO{1'b1}});
        check_frame(32'hA5A5_5A5A);
        check_frame(32'h0123_4567);

        for (int n = 0; n < 20; n++) begin
            check_frame($urandom);
        end

        if (errors != 0) begin
            $display("FAIL: tb_deserializer errors=%0d LSB_FIRST=%0b", errors, LSB_FIRST);
            $fatal(1);
        end
        $display("PASS: tb_deserializer LSB_FIRST=%0b", LSB_FIRST);
        $finish;
    end

endmodule
