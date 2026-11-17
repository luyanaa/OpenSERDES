`timescale 1ns/1ps

// PIPE-Lite digital shell loopback: 8b10b data + K-code passthrough, RxStatus,
// PowerDown gating, TxDetectRx -> PhyStatus, and the word clock period.
module tb_pipe_lite;
    logic clk, rst_n;
    logic [15:0] tx_data;
    logic [1:0]  tx_datak;
    logic tx_valid, tx_ready, tx_serial_out, tx_serial_valid, tx_usr_clk;
    logic rx_serial_in;
    logic [15:0] rx_data;
    logic [1:0]  rx_datak;
    logic rx_valid;
    logic [2:0] rx_status;
    logic rx_rec_clk;
    logic [1:0] power_down;
    logic rate, tx_elec_idle, rx_elec_idle, tx_detect_rx, phy_status;
    logic analog_tx_elec_idle, analog_rx_elec_idle, analog_tx_det_rx, rx_lock;

    int errors;
    int got_n;
    logic [15:0] got_data [0:31];
    logic [1:0]  got_k [0:31];

    always #5 clk = ~clk;

    serdes_pipe_lite_top #(
        .LSB_FIRST(1'b1), .OVERSAMPLE(1), .CDR_MODE(2), .SYNC_STAGES(2)
    ) dut (
        .clk(clk), .rst_n(rst_n),
        .tx_data(tx_data), .tx_datak(tx_datak), .tx_valid(tx_valid),
        .tx_ready(tx_ready), .tx_serial_out(tx_serial_out),
        .tx_serial_valid(tx_serial_valid), .tx_usr_clk(tx_usr_clk),
        .rx_serial_in(rx_serial_in),
        .rx_data(rx_data), .rx_datak(rx_datak), .rx_valid(rx_valid),
        .rx_status(rx_status), .rx_rec_clk(rx_rec_clk),
        .power_down(power_down), .rate(rate),
        .tx_elec_idle(tx_elec_idle), .rx_elec_idle(rx_elec_idle),
        .tx_detect_rx(tx_detect_rx), .phy_status(phy_status),
        .analog_tx_elec_idle(analog_tx_elec_idle),
        .analog_rx_elec_idle(analog_rx_elec_idle),
        .analog_tx_det_rx(analog_tx_det_rx), .rx_lock(rx_lock)
    );

    assign rx_serial_in = tx_serial_out;

    always @(posedge clk) begin
        if (rst_n && rx_valid && got_n < 32) begin
            got_data[got_n] <= rx_data;
            got_k[got_n]    <= rx_datak;
            got_n <= got_n + 1;
        end
    end

    task automatic send_word(input logic [15:0] d, input logic [1:0] k);
        begin
            @(negedge clk);
            tx_data  = d;
            tx_datak = k;
            tx_valid = 1'b1;
            while (!tx_ready) @(negedge clk);
            @(negedge clk);
            tx_valid = 1'b0;
        end
    endtask

    function automatic int find_word(input logic [15:0] d, input logic [1:0] k);
        begin
            find_word = -1;
            for (int i = 0; i < got_n; i++)
                if (got_data[i] === d && got_k[i] === k)
                    find_word = i;
        end
    endfunction

    realtime edge0, word_period;

    initial begin
        clk = 0; rst_n = 0;
        tx_data = '0; tx_datak = '0; tx_valid = 0;
        power_down = 2'b00; rate = 1'b1;
        tx_elec_idle = 0; rx_elec_idle = 0; tx_detect_rx = 0;
        errors = 0; got_n = 0; edge0 = 0; word_period = 0;

        repeat (5) @(posedge clk);
        rst_n = 1;
        repeat (6) @(posedge clk);

        send_word(16'hBCBC, 2'b11);   // COM: K28.5/K28.5 aligns the RX
        send_word(16'h1234, 2'b00);
        send_word(16'hABCD, 2'b00);
        send_word(16'hBCBC, 2'b11);   // K28.5 on both bytes
        send_word(16'h5A5A, 2'b00);
        repeat (200) @(posedge clk);
        for (int i = 0; i < got_n; i++)
            $display("DBG got[%0d] data=%04h k=%b status=%b", i, got_data[i], got_k[i], rx_status);

        if (find_word(16'h1234, 2'b00) < 0 ||
            find_word(16'hABCD, 2'b00) < 0 ||
            find_word(16'h5A5A, 2'b00) < 0) begin
            $display("FAIL: 8b10b data words not recovered (%0d words)", got_n);
            errors++;
        end
        if (find_word(16'hBCBC, 2'b11) < 0) begin
            $display("FAIL: K28.5 word not recovered with datak=11");
            errors++;
        end
        if (analog_tx_elec_idle !== 1'b0 || analog_tx_det_rx !== 1'b0) begin
            $display("FAIL: analog hooks not low in P0 idle");
            errors++;
        end

        // TxDetectRx -> one-cycle PhyStatus with RxStatus=receiver detected.
        @(negedge clk); tx_detect_rx = 1'b1;
        #1;
        if (analog_tx_det_rx !== 1'b1) begin
            $display("FAIL: analog_tx_det_rx did not follow TxDetectRx");
            errors++;
        end
        @(negedge clk); tx_detect_rx = 1'b0;
        @(posedge phy_status);
        @(posedge clk); #1;
        if (rx_status !== 3'b010) begin
            $display("FAIL: RxStatus %b after TxDetectRx, want 010", rx_status);
            errors++;
        end

        // PowerDown gates the PIPE interface.
        @(negedge clk); power_down = 2'b01;
        repeat (4) @(posedge clk);
        if (tx_ready !== 1'b0) begin
            $display("FAIL: tx_ready high in power_down");
            errors++;
        end
        if (analog_tx_elec_idle !== 1'b1) begin
            $display("FAIL: analog_tx_elec_idle low in power_down");
            errors++;
        end
        @(negedge clk); power_down = 2'b00;

        // Word clock = clk / 20.
        @(posedge rx_rec_clk); edge0 = $realtime;
        @(posedge rx_rec_clk); word_period = $realtime - edge0;
        if (word_period != 200.0) begin
            $display("FAIL: rx_rec_clk period %0t, want 200", word_period);
            errors++;
        end

        if (errors == 0)
            $display("PASS: tb_pipe_lite words=%0d word_period=%0t status=%b",
                     got_n, word_period, rx_status);
        else
            $display("FAIL: tb_pipe_lite errors=%0d", errors);
        $finish;
    end

    initial begin
        #500000;
        $display("FAIL: tb_pipe_lite timeout words=%0d", got_n);
        $finish;
    end
endmodule
