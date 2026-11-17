`timescale 1ns/1ps

// 8b/10b K-code passthrough and 10-bit K28.5 comma alignment (ALIGN_MODE=3).
// The frame starts with a K28.5 used for alignment; a second K28.5 sits in the
// payload and must come back with rx_charisk=1.
module tb_8b10b_kcodes;
    localparam int DW = 8;
    localparam int WORDS = 8;

    logic tx_clk, tx_rst_n, tx_enable, tx_load;
    logic [WORDS-1:0][DW-1:0] tx_data;
    logic [WORDS-1:0] tx_charisk;
    logic tx_ready, tx_serial_out, tx_serial_valid, tx_busy, tx_frame_done;
    logic rx_clk, rx_rst_n, rx_enable, rx_flush, rx_align, rx_serial_in;
    logic [WORDS-1:0][DW-1:0] rx_data;
    logic [WORDS-1:0] rx_charisk;
    logic rx_par_valid, rx_busy, rx_lock, rx_lock_stale, rx_sample_strobe;
    logic rx_clk_out, rx_frame_locked, rx_frame_valid, rx_serial_data, rx_serial_valid;
    logic [12:0] rx_freq_trim;
    logic code_error;

    int errors;
    int code_errs;

    always #10 tx_clk = ~tx_clk;
    always #10 rx_clk = ~rx_clk;

    serdes_8b10b_top #(
        .DATA_WIDTH(DW), .WORDS(WORDS), .LSB_FIRST(1'b1),
        .CAPTURE_MODE(1'b0), .OVERSAMPLE(1), .CDR_MODE(2), .CDR_BYPASS(1'b1),
        .ALIGN_MODE(3), .SYNC_LEN(8), .SYNC_PATTERN(8'hBC)
    ) dut (
        .tx_clk(tx_clk), .tx_rst_n(tx_rst_n), .tx_enable(tx_enable),
        .tx_load(tx_load), .tx_par_data(tx_data), .tx_charisk(tx_charisk),
        .tx_ready(tx_ready), .tx_serial_out(tx_serial_out),
        .tx_serial_valid(tx_serial_valid), .tx_busy(tx_busy),
        .tx_frame_done(tx_frame_done),
        .rx_clk(rx_clk), .rx_rst_n(rx_rst_n), .rx_enable(rx_enable),
        .rx_flush(rx_flush), .rx_align(rx_align), .rx_serial_in(rx_serial_in),
        .rx_par_data(rx_data), .rx_charisk(rx_charisk),
        .rx_par_valid(rx_par_valid), .rx_busy(rx_busy),
        .rx_lock(rx_lock), .rx_lock_stale(rx_lock_stale),
        .rx_sample_strobe(rx_sample_strobe), .rx_clk_out(rx_clk_out),
        .rx_frame_locked(rx_frame_locked), .rx_frame_valid(rx_frame_valid),
        .rx_serial_data(rx_serial_data), .rx_serial_valid(rx_serial_valid),
        .rx_freq_trim(rx_freq_trim), .code_error(code_error)
    );

    assign rx_serial_in = tx_serial_out;

    always @(posedge tx_clk) if (tx_rst_n && code_error) begin
        code_errs++;
        $display("DBG tx code_error state=%0d idx=%0d data=%02h ctrl=%b",
                 dut.tx_state_q, dut.tx_idx_q, dut.enc_data, dut.enc_ctrl);
    end
    always @(posedge rx_clk) if (dut.comma_pulse)
        $display("DBG comma_pulse rd_plus=%b locked=%b", dut.comma_rd_plus, dut.comma_locked);
    always @(posedge rx_clk) if (dut.dec_data_valid)
        $display("DBG dec code=%03h data=%h ctrl=%b rd=%b disperr=%b", dut.dec_code, dut.dec_data, dut.dec_ctrl, dut.u_dec.u_dec.rd, dut.dec_disp_error);

    initial begin
        tx_clk = 0; rx_clk = 0;
        tx_rst_n = 0; rx_rst_n = 0;
        tx_enable = 0; tx_load = 0; rx_enable = 0; rx_flush = 0; rx_align = 0;
        tx_data = '0; tx_charisk = '0;
        errors = 0; code_errs = 0;

        repeat (4) @(posedge tx_clk);
        tx_rst_n = 1; rx_rst_n = 1;
        repeat (4) @(posedge tx_clk);
        tx_enable = 1; rx_enable = 1;

        // word0 K28.5 aligns; word2 K28.5 is payload.
        tx_data[0] = 8'hBC; tx_data[1] = 8'h11; tx_data[2] = 8'hBC;
        tx_data[3] = 8'h33; tx_data[4] = 8'h44; tx_data[5] = 8'h55;
        tx_data[6] = 8'h66; tx_data[7] = 8'h77;
        tx_charisk = 8'b0000_0101;

        while (!tx_ready) @(posedge tx_clk);
        @(negedge tx_clk); tx_load = 1'b1;
        @(negedge tx_clk); tx_load = 1'b0;

        while (!rx_par_valid) @(posedge rx_clk);
        #1;

        // The first K28.5 is consumed by alignment, so the payload starts at
        // word1: data[0..6] and charisk[1] must be the second K28.5.
        if (rx_charisk[1] !== 1'b1) begin
            $display("FAIL: rx_charisk[1]=%b, want K code", rx_charisk[1]);
            errors++;
        end
        if (rx_data[0] !== 8'h11 || rx_data[1] !== 8'hBC ||
            rx_data[2] !== 8'h33 || rx_data[3] !== 8'h44 ||
            rx_data[4] !== 8'h55 || rx_data[5] !== 8'h66 ||
            rx_data[6] !== 8'h77) begin
            $display("FAIL: payload %h %h %h %h %h %h %h",
                     rx_data[0], rx_data[1], rx_data[2], rx_data[3],
                     rx_data[4], rx_data[5], rx_data[6]);
            errors++;
        end
        if (!rx_frame_locked) begin
            $display("FAIL: frame not locked after K28.5");
            errors++;
        end

        if (errors == 0)
            $display("PASS: tb_8b10b_kcodes charisk=%b code_errs=%0d locked=%0d",
                     rx_charisk, code_errs, rx_frame_locked);
        else
            $display("FAIL: tb_8b10b_kcodes errors=%0d", errors);
        $finish;
    end

    initial begin
        #200000;
        $display("FAIL: timeout");
        $finish;
    end
endmodule
