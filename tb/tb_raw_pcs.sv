`timescale 1ns/1ps

// Raw PCS loopback: flat word bus, comma alignment, rx_slide recovery and the
// divided word clock.  The receiver is sampled continuously while the TX
// streams, and the captured words are searched for the expected frame because
// the deserializer free-runs until the comma aligns it.
module tb_raw_pcs #(
    parameter int DATA_WIDTH = 16
);
    localparam logic [7:0] COMMA = 8'hBC;
    localparam int MAX_WORDS = 64;

    logic clk, rst_n;
    logic [DATA_WIDTH-1:0] tx_data;
    logic tx_valid, tx_ready, tx_serial_out, tx_serial_valid, tx_usr_clk;
    logic rx_serial_in;
    logic [DATA_WIDTH-1:0] rx_data;
    logic rx_valid, rx_rec_clk, rx_slide, rx_align_bypass;
    logic rx_lock, rx_busy, rx_frame_locked, rx_clk_out;

    int errors;
    logic [DATA_WIDTH-1:0] got [0:MAX_WORDS-1];
    int got_n;

    always #5 clk = ~clk;

    serdes_raw_pcs_top #(
        .DATA_WIDTH(DATA_WIDTH),
        .LSB_FIRST(1'b1),
        .OVERSAMPLE(1),
        .CDR_MODE(2),
        .SYNC_LEN(DATA_WIDTH),
        .SYNC_PATTERN(DATA_WIDTH'(COMMA)),
        .SYNC_STAGES(2)
    ) dut (
        .clk(clk), .rst_n(rst_n),
        .tx_data(tx_data), .tx_valid(tx_valid), .tx_ready(tx_ready),
        .tx_serial_out(tx_serial_out), .tx_serial_valid(tx_serial_valid),
        .tx_usr_clk(tx_usr_clk),
        .rx_serial_in(rx_serial_in),
        .rx_data(rx_data), .rx_valid(rx_valid), .rx_rec_clk(rx_rec_clk),
        .rx_slide(rx_slide), .rx_align_bypass(rx_align_bypass),
        .rx_lock(rx_lock), .rx_busy(rx_busy),
        .rx_frame_locked(rx_frame_locked), .rx_clk_out(rx_clk_out)
    );

    assign rx_serial_in = tx_serial_out;

    always @(posedge clk) begin
        if (rst_n && rx_valid && got_n < MAX_WORDS) begin
            got[got_n] <= rx_data;
            got_n <= got_n + 1;
        end
    end

    task automatic send_word(input logic [DATA_WIDTH-1:0] w);
        begin
            @(negedge clk);
            tx_data  = w;
            tx_valid = 1'b1;
            while (!tx_ready) @(negedge clk);
        end
    endtask

    function automatic int find_run(input int start,
                                    input logic [DATA_WIDTH-1:0] a,
                                    input logic [DATA_WIDTH-1:0] b);
        begin
            find_run = -1;
            for (int i = start; i < got_n - 1; i++) begin
                if (got[i] === a && got[i+1] === b)
                    find_run = i;
            end
        end
    endfunction

    realtime word_edge, word_period;

    initial begin
        clk = 0; rst_n = 0;
        tx_data = '0; tx_valid = 0;
        rx_slide = 0; rx_align_bypass = 0;
        errors = 0; got_n = 0;
        word_edge = 0; word_period = 0;

        repeat (5) @(posedge clk);
        rst_n = 1;
        repeat (4) @(posedge clk);

        // Frame 0.
        send_word(DATA_WIDTH'(COMMA));
        send_word(16'h1234);
        send_word(16'h5678);
        send_word(16'h9ABC);
        send_word(16'hDEF0);
        repeat (4 * DATA_WIDTH + 8) @(posedge clk);

        if (find_run(0, 16'h1234, 16'h5678) < 0) begin
            $display("FAIL: frame0 payload not recovered (%0d words)", got_n);
            errors++;
        end

        // One-cycle bitslip, then a fresh comma re-aligns.
        @(negedge clk); rx_slide = 1'b1;
        @(negedge clk); rx_slide = 1'b0;
        repeat (4) @(posedge clk);

        send_word(DATA_WIDTH'(COMMA));
        send_word(16'h0F0F);
        send_word(16'hF0F0);
        send_word(16'hAAAA);
        send_word(16'h5555);
        repeat (4 * DATA_WIDTH + 8) @(posedge clk);

        if (find_run(0, 16'h0F0F, 16'hF0F0) < 0) begin
            $display("FAIL: frame1 payload not recovered after slide (%0d words)",
                     got_n);
            errors++;
        end
        if (!rx_frame_locked) begin
            $display("FAIL: rx_frame_locked low after comma");
            errors++;
        end

        // Word clock must be clk / DATA_WIDTH.
        @(posedge tx_usr_clk); word_edge = $realtime;
        @(posedge tx_usr_clk); word_period = $realtime - word_edge;
        if (word_period != 10.0 * DATA_WIDTH) begin
            $display("FAIL: tx_usr_clk period %0t ns, want %0d ns",
                     word_period, 10 * DATA_WIDTH);
            errors++;
        end

        if (errors == 0)
            $display("PASS: tb_raw_pcs W=%0d words=%0d word_period=%0t frame_locked=%0d",
                     DATA_WIDTH, got_n, word_period, rx_frame_locked);
        else
            $display("FAIL: tb_raw_pcs errors=%0d", errors);
        $finish;
    end

    initial begin
        #400000;
        $display("FAIL: tb_raw_pcs timeout words=%0d", got_n);
        $finish;
    end
endmodule
