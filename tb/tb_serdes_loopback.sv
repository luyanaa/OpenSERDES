`timescale 1ns/1ps

`ifndef TB_LSB_FIRST
`define TB_LSB_FIRST 1
`endif

module tb_serdes_loopback #(
    parameter int DATA_WIDTH = 8,
    parameter int WORDS      = 4,
    parameter bit LSB_FIRST  = (`TB_LSB_FIRST != 0)
);

    localparam int RATIO = DATA_WIDTH * WORDS;

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
    int done_seen;

    serializer #(
        .DATA_WIDTH(DATA_WIDTH),
        .WORDS(WORDS),
        .LSB_FIRST(LSB_FIRST)
    ) tx (
        .clk(clk),
        .rst_n(rst_n),
        .enable(enable),
        .load(load),
        .par_data(tx_data),
        .ready(tx_ready),
        .serial_out(serial_line),
        .serial_valid(serial_valid),
        .busy(tx_busy),
        .frame_done(tx_done)
    );

    deserializer #(
        .DATA_WIDTH(DATA_WIDTH),
        .WORDS(WORDS),
        .LSB_FIRST(LSB_FIRST)
    ) rx (
        .clk(clk),
        .rst_n(rst_n),
        .enable(enable),
        .align(1'b0),
        .flush(1'b0),
        .serial_in(serial_line),
        .serial_valid(serial_valid),
        .par_data(rx_data),
        .par_valid(rx_valid),
        .busy(rx_busy)
    );

    always #5 clk = ~clk;

    always @(posedge clk) begin
        if (tx_done)
            done_seen++;
    end

    task automatic run_frame(input logic [RATIO-1:0] data);
        int timeout;
        begin
            @(negedge clk);
            if (!tx_ready) begin
                $display("ERROR: tx not ready");
                errors++;
            end
            for (int w = 0; w < WORDS; w++)
                tx_data[w] = data[w*DATA_WIDTH +: DATA_WIDTH];
            load    = 1'b1;
            @(posedge clk);
            #1;
            load = 1'b0;
            enable = 1'b1;

            timeout = 0;
            while (!rx_valid && timeout < (RATIO*4)) begin
                @(posedge clk);
                #1;
                timeout++;
            end

            if (!rx_valid) begin
                $display("ERROR: rx_valid timeout");
                errors++;
            end
            for (int w = 0; w < WORDS; w++) begin
                if (rx_data[w] !== data[w*DATA_WIDTH +: DATA_WIDTH]) begin
                    $display("ERROR: loopback word %0d=%h expected=%h",
                             w, rx_data[w], data[w*DATA_WIDTH +: DATA_WIDTH]);
                    errors++;
                end
            end

            enable = 1'b0;
            @(posedge clk);
            #1;
            if (rx_valid) begin
                $display("ERROR: rx_valid did not deassert");
                errors++;
            end
        end
    endtask

    initial begin
        clk       = 1'b0;
        rst_n     = 1'b0;
        enable    = 1'b0;
        load      = 1'b0;
        tx_data   = '0;
        errors    = 0;
        done_seen = 0;

        repeat (3) @(posedge clk);
        rst_n = 1'b1;
        repeat (2) @(posedge clk);

        run_frame('0);
        run_frame('1);
        run_frame({RATIO{1'b1}});
        run_frame(32'hA5A5_5A5A);

        for (int n = 0; n < 20; n++) begin
            run_frame($urandom);
        end

        if (done_seen != 24) begin
            $display("ERROR: tx frame_done count=%0d expected=24", done_seen);
            errors++;
        end
        if (errors != 0) begin
            $display("FAIL: tb_serdes_loopback errors=%0d LSB_FIRST=%0b", errors, LSB_FIRST);
            $fatal(1);
        end
        $display("PASS: tb_serdes_loopback LSB_FIRST=%0b", LSB_FIRST);
        $finish;
    end

endmodule
