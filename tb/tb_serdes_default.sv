`timescale 1ns/1ps

module tb_serdes_default;

    localparam int DATA_WIDTH = 32;
    localparam int WORDS      = 8;
    localparam int RATIO      = DATA_WIDTH * WORDS;

    logic clk;
    logic rst_n;
    logic enable;
    logic load;
    logic [WORDS-1:0][DATA_WIDTH-1:0] tx_data;
    logic tx_ready;
    logic serial_line;
    logic serial_valid;
    logic tx_busy;
    logic tx_done;

    logic [WORDS-1:0][DATA_WIDTH-1:0] rx_data;
    logic rx_valid;
    logic rx_busy;

    int errors;

    serializer #(
        .DATA_WIDTH(DATA_WIDTH),
        .WORDS(WORDS),
        .LSB_FIRST(1'b1)
    ) tx (
        .clk(clk), .rst_n(rst_n), .enable(enable), .load(load),
        .par_data(tx_data), .ready(tx_ready), .serial_out(serial_line),
        .serial_valid(serial_valid), .busy(tx_busy), .frame_done(tx_done)
    );

    deserializer #(
        .DATA_WIDTH(DATA_WIDTH),
        .WORDS(WORDS),
        .LSB_FIRST(1'b1)
    ) rx (
        .clk(clk), .rst_n(rst_n), .enable(enable), .align(1'b0), .flush(1'b0),
        .serial_in(serial_line), .serial_valid(serial_valid),
        .par_data(rx_data), .par_valid(rx_valid), .busy(rx_busy)
    );

    always #5 clk = ~clk;

    task automatic run_frame(input logic [RATIO-1:0] data);
        int timeout;
        begin
            @(negedge clk);
            for (int w = 0; w < WORDS; w++)
                tx_data[w] = data[w*DATA_WIDTH +: DATA_WIDTH];
            load    = 1'b1;
            @(posedge clk);
            #1;
            load   = 1'b0;
            enable = 1'b1;

            timeout = 0;
            while (!rx_valid && timeout < (RATIO*4)) begin
                @(posedge clk);
                #1;
                timeout++;
            end
            if (!rx_valid) begin
                $display("ERROR: default loopback rx_valid timeout");
                errors++;
            end else begin
                for (int w = 0; w < WORDS; w++) begin
                    if (rx_data[w] !== data[w*DATA_WIDTH +: DATA_WIDTH]) begin
                        $display("ERROR: default loopback word %0d mismatch", w);
                        errors++;
                    end
                end
            end

            enable = 1'b0;
            @(posedge clk);
            #1;
        end
    endtask

    initial begin
        clk     = 1'b0;
        rst_n   = 1'b0;
        enable  = 1'b0;
        load    = 1'b0;
        tx_data = '0;
        errors  = 0;

        repeat (3) @(posedge clk);
        rst_n = 1'b1;
        repeat (2) @(posedge clk);

        run_frame('0);
        run_frame({RATIO{1'b1}});
        run_frame({8{$urandom}});
        run_frame({8{$urandom}});

        if (errors != 0) begin
            $display("FAIL: tb_serdes_default errors=%0d", errors);
            $fatal(1);
        end
        $display("PASS: tb_serdes_default 32x8");
        $finish;
    end

endmodule
