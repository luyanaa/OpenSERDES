`timescale 1ns/1ps

module tb_serdes_training;
    localparam int DW = 8;
    localparam int WORDS = 2;
    localparam int OS = 4;
    localparam int BEST_PHASE = 2;

    logic tx_clk = 0, rx_clk = 0;
    logic rst_n = 0;
    logic tx_enable = 0, tx_load = 0;
    logic [WORDS-1:0][DW-1:0] tx_par_data = '0;
    logic rx_enable = 1, rx_flush = 0, rx_align = 0, rx_serial_in = 0;
    logic [15:0] train_error_count = '0;
    logic train_start = 0;

    logic tx_ready, tx_serial_out, tx_serial_valid, tx_busy, tx_frame_done;
    logic [WORDS-1:0][DW-1:0] rx_par_data;
    logic rx_par_valid, rx_busy, rx_lock, rx_lock_stale, rx_sample_strobe;
    logic rx_clk_out, rx_frame_locked, rx_frame_valid, rx_serial_data, rx_serial_valid;
    logic signed [12:0] rx_freq_trim;
    logic train_locked, train_busy, train_retrain, train_phase_we, train_thresh_we;
    logic [1:0] train_phase, train_best_phase;
    logic [15:0] train_best_errors;

    always #5 tx_clk = ~tx_clk;
    always #5 rx_clk = ~rx_clk;

    serdes_digital_top #(
        .DATA_WIDTH(DW), .WORDS(WORDS), .OVERSAMPLE(OS),
        .CDR_MODE(0), .TRAINING_ENABLE(1'b1), .TRAIN_DWELL_CYCLES(8),
        .TRAIN_ERROR_WIDTH(16)
    ) dut (
        .tx_clk(tx_clk), .tx_rst_n(rst_n), .tx_enable(tx_enable),
        .tx_load(tx_load), .tx_par_data(tx_par_data), .tx_ready(tx_ready),
        .tx_serial_out(tx_serial_out), .tx_serial_valid(tx_serial_valid),
        .tx_busy(tx_busy), .tx_frame_done(tx_frame_done),
        .rx_clk(rx_clk), .rx_rst_n(rst_n), .rx_enable(rx_enable),
        .rx_flush(rx_flush), .rx_align(rx_align), .rx_serial_in(rx_serial_in),
        .rx_par_data(rx_par_data), .rx_par_valid(rx_par_valid), .rx_busy(rx_busy),
        .rx_lock(rx_lock), .rx_lock_stale(rx_lock_stale),
        .rx_sample_strobe(rx_sample_strobe), .rx_clk_out(rx_clk_out),
        .rx_frame_locked(rx_frame_locked), .rx_frame_valid(rx_frame_valid),
        .rx_serial_data(rx_serial_data), .rx_serial_valid(rx_serial_valid),
        .rx_freq_trim(rx_freq_trim),
        .train_start(train_start), .train_error_count(train_error_count),
        .train_locked(train_locked), .train_busy(train_busy),
        .train_retrain(train_retrain), .train_phase_we(train_phase_we),
        .train_thresh_we(train_thresh_we), .train_phase(train_phase),
        .train_best_phase(train_best_phase), .train_best_errors(train_best_errors)
    );

    always_ff @(posedge rx_clk or negedge rst_n) begin
        if (!rst_n)
            train_error_count <= '0;
        else if (train_busy && train_phase != BEST_PHASE[1:0])
            train_error_count <= train_error_count + 1'b1;
    end

    initial begin
        repeat (3) @(posedge rx_clk);
        rst_n = 1'b1;
        repeat (2) @(posedge rx_clk);
        @(negedge rx_clk); train_start = 1'b1;
        @(negedge rx_clk); train_start = 1'b0;

        repeat (200) @(posedge rx_clk);
        if (!train_locked) begin
            $display("FAIL training did not lock phase=%0d best=%0d state busy=%0d",
                     train_phase, train_best_phase, train_busy);
            $finish;
        end
        if (train_best_phase != BEST_PHASE[1:0]) begin
            $display("FAIL best phase=%0d expected=%0d", train_best_phase, BEST_PHASE);
            $finish;
        end
        $display("PASS training locked best_phase=%0d phase=%0d errors=%0d",
                 train_best_phase, train_phase, train_best_errors);
        $finish;
    end

    initial begin
        #5000;
        $display("FAIL timeout");
        $finish;
    end
endmodule
