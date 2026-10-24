`timescale 1ns/1ps

// Receiver-only mode: CDR bypassed, external rx_clk is the bit clock.
// The receiver sampler and deserializer must recover the same 8 x 32 frame.
module tb_cdr_bypass;

    logic clk = 0;
    logic rst_n = 0;
    logic enable = 0;
    logic serial_in = 0;

    logic [7:0][31:0] par_data;
    logic par_valid;
    logic busy;
    logic lock;
    logic sample_strobe;
    logic clk_out;

    logic [31:0] words [0:7];
    logic expected [0:255];
    logic valid_seen;
    integer i, w, b, errors;

    always #2.5 clk = ~clk;

    serdes_rx_digital #(
        .DATA_WIDTH(32),
        .WORDS(8),
        .LSB_FIRST(1'b1),
        .OVERSAMPLE(4),
        .CDR_BYPASS(1'b1)
    ) dut (
        .rx_clk(clk),
        .rx_rst_n(rst_n),
        .rx_enable(enable),
        .rx_flush(1'b0),
        .rx_align(1'b0),
        .rx_serial_in(serial_in),
        .rx_par_data(par_data),
        .rx_par_valid(par_valid),
        .rx_busy(busy),
        .rx_lock(lock),
        .rx_sample_strobe(sample_strobe),
        .rx_clk_out(clk_out)
    );

    always @(posedge clk) begin
        if (par_valid)
            valid_seen <= 1'b1;
    end

    initial begin
        errors = 0;
        valid_seen = 0;

        words[0] = 32'h0123_4567;
        words[1] = 32'h89AB_CDEF;
        words[2] = 32'hFEDC_BA98;
        words[3] = 32'h7654_3210;
        words[4] = 32'hA5A5_5A5A;
        words[5] = 32'h0F0F_F0F0;
        words[6] = 32'h3333_CCCC;
        words[7] = 32'h8000_0001;

        for (w = 0; w < 8; w = w + 1)
            for (b = 0; b < 32; b = b + 1)
                expected[w*32 + b] = words[w][b];

        rst_n = 0;
        enable = 0;
        repeat (3) @(posedge clk);
        rst_n = 1;
        repeat (4) @(posedge clk);
        enable = 1;

        for (i = 0; i < 256; i = i + 1) begin
            serial_in = expected[i];
            @(posedge clk);
        end

        // Stop accepting bits after the frame.  The first captured bit is
        // aligned by the sampler; no extra dummy bit may enter the frame.
        enable = 0;
        repeat (8) @(posedge clk);

        if (!valid_seen) begin
            $display("FAIL: bypass par_valid never asserted");
            errors = errors + 1;
        end
        if (!lock || clk_out !== clk) begin
            $display("FAIL: bypass lock/clk_out wrong: lock=%b clk_out=%b", lock, clk_out);
            errors = errors + 1;
        end
        if (par_data !== {words[7], words[6], words[5], words[4],
                          words[3], words[2], words[1], words[0]}) begin
            $display("FAIL: bypass words %h %h %h %h %h %h %h %h",
                     par_data[0], par_data[1], par_data[2], par_data[3],
                     par_data[4], par_data[5], par_data[6], par_data[7]);
            errors = errors + 1;
        end

        if (errors == 0)
            $display("PASS: tb_cdr_bypass");
        else
            $display("FAIL: tb_cdr_bypass errors=%0d", errors);
        $finish;
    end

endmodule
