`timescale 1ns/1ps

`ifndef TB_LSB_FIRST
`define TB_LSB_FIRST 1
`endif
`ifndef TB_CAPTURE_MODE
`define TB_CAPTURE_MODE 0
`endif

module tb_serializer;

    localparam int DATA_WIDTH = 8;
    localparam int WORDS      = 4;
    localparam int RATIO      = DATA_WIDTH * WORDS;
    localparam bit LSB_FIRST  = (`TB_LSB_FIRST != 0);
    localparam bit CAPTURE_MODE = (`TB_CAPTURE_MODE != 0);

    logic clk;
    logic rst_n;
    logic enable;
    logic load;
    logic [WORDS-1:0][DATA_WIDTH-1:0] par_data;
    logic ready;
    logic serial_out;
    logic serial_valid;
    logic busy;
    logic frame_done;

    int errors;

    serializer #(
        .DATA_WIDTH(DATA_WIDTH),
        .WORDS(WORDS),
        .LSB_FIRST(LSB_FIRST),
        .CAPTURE_MODE(CAPTURE_MODE)
    ) dut (
        .clk(clk),
        .rst_n(rst_n),
        .enable(enable),
        .load(load),
        .par_data(par_data),
        .ready(ready),
        .serial_out(serial_out),
        .serial_valid(serial_valid),
        .busy(busy),
        .frame_done(frame_done)
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
        logic exp;
        begin
            @(negedge clk);
            if (!ready) begin
                $display("ERROR: ready low before load");
                errors++;
            end
            load     = 1'b1;
            for (int w = 0; w < WORDS; w++)
                par_data[w] = data[w*DATA_WIDTH +: DATA_WIDTH];
            @(posedge clk);
            #1;
            load = 1'b0;
            enable = 1'b1;

            begin : wait_start
                int timeout;
                timeout = 0;
                while (!serial_valid && timeout < (WORDS + 4)) begin
                    @(posedge clk);
                    #1;
                    timeout++;
                end
                if (!serial_valid) begin
                    $display("ERROR: frame did not start");
                    errors++;
                end
            end

            for (int i = 0; i < RATIO; i++) begin
                enable = 1'b1;
                exp = expected_bit(data, i);
                if (!serial_valid) begin
                    $display("ERROR: serial_valid low at bit %0d", i);
                    errors++;
                end
                if (serial_out !== exp) begin
                    $display("ERROR: bit %0d got %0b expected %0b", i, serial_out, exp);
                    errors++;
                end
                @(posedge clk);
                #1;
            end

            enable = 1'b0;

            if (!frame_done) begin
                $display("ERROR: frame_done missing");
                errors++;
            end
            if (busy || serial_valid) begin
                $display("ERROR: frame still active after last bit");
                errors++;
            end

            @(posedge clk);
            #1;
            if (frame_done) begin
                $display("ERROR: frame_done did not pulse for one cycle");
                errors++;
            end
            if (!ready) begin
                $display("ERROR: ready did not return");
                errors++;
            end
        end
    endtask

    initial begin
        clk      = 1'b0;
        rst_n    = 1'b0;
        enable   = 1'b0;
        load     = 1'b0;
        par_data = '0;
        errors   = 0;

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
            $display("FAIL: tb_serializer errors=%0d LSB_FIRST=%0b", errors, LSB_FIRST);
            $fatal(1);
        end
        $display("PASS: tb_serializer LSB_FIRST=%0b CAPTURE_MODE=%0b", LSB_FIRST, CAPTURE_MODE);
        $finish;
    end

endmodule
