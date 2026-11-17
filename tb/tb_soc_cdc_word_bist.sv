`timescale 1ns/1ps

// Word-BIST path of serdes_soc_cdc_wrapper: with CSR bit 6 set, the RX payload
// stream is consumed by prbs_word_check (not the AXIS FIFO) and the checker's
// error count is added to the training FSM's error input in rx_clk.
module tb_soc_cdc_word_bist;
    localparam int DW = 8;
    localparam int WORDS = 4;
    localparam logic [7:0] COMMA = 8'hBC;

    logic pclk, presetn;
    logic [15:0] paddr;
    logic psel, penable, pwrite;
    logic [31:0] pwdata, prdata;
    logic pready, pslverr;
    logic s_axis_aclk, s_axis_aresetn, s_axis_tvalid, s_axis_tready;
    logic [DW-1:0] s_axis_tdata;
    logic s_axis_tlast;
    logic m_axis_aclk, m_axis_aresetn, m_axis_tvalid, m_axis_tready;
    logic [DW-1:0] m_axis_tdata;
    logic m_axis_tlast;
    logic tx_clk, tx_rst_n, tx_serial_out, tx_serial_valid;
    logic rx_clk, rx_rst_n, rx_serial_in;
    logic rx_sample_strobe, rx_serial_data, rx_serial_valid;
    logic signed [12:0] rx_freq_trim;
    logic rx_clk_out;
    logic [15:0][31:0] csr_regs;
    logic train_start;
    logic [15:0] train_error_count;
    logic train_locked, train_busy, train_retrain;
    logic [0:0] train_phase, train_best_phase;
    logic [15:0] train_best_errors;
    int errors;
    int frames_sent;

    always #5 pclk = ~pclk;
    always #5 s_axis_aclk = ~s_axis_aclk;
    always #7 m_axis_aclk = ~m_axis_aclk;
    always #10 tx_clk = ~tx_clk;
    always #5 rx_clk = ~rx_clk;

    serdes_soc_cdc_wrapper #(
        .DATA_WIDTH(DW), .WORDS(WORDS), .LSB_FIRST(1'b1),
        .SYNC_LEN(8), .SYNC_PATTERN(COMMA), .FIFO_DEPTH(16),
        .BIST_PRBS_WIDTH(7), .BIST_SEED(7'h7F),
        .OVERSAMPLE(1), .CDR_MODE(0), .TRAINING_ENABLE(1'b1),
        .TRAIN_DWELL_CYCLES(8), .TRAIN_ERROR_WIDTH(16)
    ) dut (
        .pclk(pclk), .presetn(presetn),
        .paddr(paddr), .psel(psel), .penable(penable), .pwrite(pwrite),
        .pwdata(pwdata), .pready(pready), .prdata(prdata), .pslverr(pslverr),
        .s_axis_aclk(s_axis_aclk), .s_axis_aresetn(s_axis_aresetn),
        .s_axis_tvalid(s_axis_tvalid), .s_axis_tready(s_axis_tready),
        .s_axis_tdata(s_axis_tdata), .s_axis_tlast(s_axis_tlast),
        .m_axis_aclk(m_axis_aclk), .m_axis_aresetn(m_axis_aresetn),
        .m_axis_tvalid(m_axis_tvalid), .m_axis_tready(m_axis_tready),
        .m_axis_tdata(m_axis_tdata), .m_axis_tlast(m_axis_tlast),
        .tx_clk(tx_clk), .tx_rst_n(tx_rst_n), .tx_serial_out(tx_serial_out),
        .tx_serial_valid(tx_serial_valid),
        .rx_clk(rx_clk), .rx_rst_n(rx_rst_n), .rx_serial_in(rx_serial_in),
        .rx_sample_strobe(rx_sample_strobe), .rx_serial_data(rx_serial_data),
        .rx_serial_valid(rx_serial_valid), .rx_freq_trim(rx_freq_trim),
        .rx_clk_out(rx_clk_out), .csr_regs(csr_regs),
        .train_start(train_start), .train_error_count(train_error_count),
        .train_locked(train_locked), .train_busy(train_busy),
        .train_retrain(train_retrain), .train_phase(train_phase),
        .train_best_phase(train_best_phase), .train_best_errors(train_best_errors)
    );

    task automatic apb_write(input logic [15:0] addr, input logic [31:0] data);
        begin
            @(negedge pclk);
            psel = 1'b1; penable = 1'b0; pwrite = 1'b1;
            paddr = addr; pwdata = data;
            @(posedge pclk); @(negedge pclk);
            penable = 1'b1;
            @(posedge pclk); #1; @(negedge pclk);
            psel = 1'b0; penable = 1'b0; pwrite = 1'b0;
        end
    endtask

    task automatic send_bit(input logic b);
        begin
            @(negedge rx_clk);
            rx_serial_in = b;
        end
    endtask

    task automatic send_word(input logic [DW-1:0] w);
        begin
            for (int i = 0; i < DW; i++)
                send_bit(w[i]);
        end
    endtask

    task automatic send_frame(input logic [DW-1:0] w0, w1, w2, w3);
        begin
            send_word(COMMA);
            send_word(w0);
            send_word(w1);
            send_word(w2);
            send_word(w3);
            frames_sent++;
        end
    endtask

    initial begin
        pclk = 0; s_axis_aclk = 0; m_axis_aclk = 0;
        tx_clk = 0; rx_clk = 0;
        presetn = 0; s_axis_aresetn = 0; m_axis_aresetn = 0;
        tx_rst_n = 0; rx_rst_n = 0;
        paddr = 0; psel = 0; penable = 0; pwrite = 0; pwdata = 0;
        s_axis_tvalid = 0; s_axis_tdata = 0; s_axis_tlast = 0;
        m_axis_tready = 1; rx_serial_in = 0;
        train_start = 0; train_error_count = '0; errors = 0; frames_sent = 0;

        repeat (4) @(posedge rx_clk);
        presetn = 1; s_axis_aresetn = 1; m_axis_aresetn = 1;
        tx_rst_n = 1; rx_rst_n = 1;
        repeat (4) @(posedge pclk);

        // RX enable + word-BIST mode (csr[8] bit 6).
        apb_write(16'h0000, 32'h0000_0002);
        apb_write(16'h0020, 32'h0000_0040);
        repeat (4) @(posedge rx_clk);

        // Three frames of non-PRBS payload: the checker must flag every bit.
        send_frame(8'h11, 8'h22, 8'h33, 8'h44);
        repeat (4) @(posedge rx_clk);
        send_frame(8'h55, 8'h66, 8'h77, 8'h88);
        repeat (4) @(posedge rx_clk);
        send_frame(8'h99, 8'hAA, 8'hBB, 8'hCC);
        repeat (8) @(posedge rx_clk);

        if (dut.u_bist_word_check.frame_count == 0) begin
            $display("FAIL: word checker saw no frames (frames_sent=%0d)", frames_sent);
            errors++;
        end
        if (dut.bist_word_error_count == 0) begin
            $display("FAIL: word checker counted no errors");
            errors++;
        end
        if (dut.train_error_total !=
            dut.train_error_count + 16'(dut.bist_word_error_count)) begin
            $display("FAIL: training error total does not include the word checker");
            errors++;
        end
        if (m_axis_tvalid) begin
            $display("FAIL: word-BIST mode still pushed a frame into the AXIS FIFO");
            errors++;
        end

        if (errors == 0)
            $display("PASS: tb_soc_cdc_word_bist frames=%0d word_errors=%0d",
                     dut.u_bist_word_check.frame_count, dut.bist_word_error_count);
        else
            $display("FAIL: tb_soc_cdc_word_bist errors=%0d", errors);
        $finish;
    end

    initial begin
        #500000;
        $display("FAIL: tb_soc_cdc_word_bist timeout");
        $finish;
    end
endmodule
