`timescale 1ns/1ps

module tb_soc_cdc_training;
    localparam int DW = 8;
    localparam int WORDS = 4;
    localparam int OS = 4;
    localparam int BEST = 2;

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
    logic [1:0] train_phase, train_best_phase;
    logic [15:0] train_best_errors;
    int errors;

    always #5 pclk = ~pclk;
    always #5 s_axis_aclk = ~s_axis_aclk;
    always #7 m_axis_aclk = ~m_axis_aclk;
    always #10 tx_clk = ~tx_clk;
    always #5 rx_clk = ~rx_clk;

    serdes_soc_cdc_wrapper #(
        .DATA_WIDTH(DW), .WORDS(WORDS), .LSB_FIRST(1'b1),
        .SYNC_LEN(8), .SYNC_PATTERN(8'hBC), .FIFO_DEPTH(16),
        .BIST_PRBS_WIDTH(7), .BIST_SEED(7'h7F),
        .OVERSAMPLE(OS), .CDR_MODE(0), .TRAINING_ENABLE(1'b1),
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

    always_ff @(posedge rx_clk or negedge rx_rst_n) begin
        if (!rx_rst_n)
            train_error_count <= '0;
        else if (train_busy && train_phase != BEST[1:0])
            train_error_count <= train_error_count + 1'b1;
    end

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

    task automatic apb_read(input logic [15:0] addr, output logic [31:0] data);
        begin
            @(negedge pclk);
            psel = 1'b1; penable = 1'b0; pwrite = 1'b0; paddr = addr;
            @(posedge pclk); @(negedge pclk);
            penable = 1'b1; #1; data = prdata;
            @(posedge pclk); @(negedge pclk);
            psel = 1'b0; penable = 1'b0;
        end
    endtask

    initial begin
        logic [31:0] v;
        pclk = 0; s_axis_aclk = 0; m_axis_aclk = 0;
        tx_clk = 0; rx_clk = 0;
        presetn = 0; s_axis_aresetn = 0; m_axis_aresetn = 0;
        tx_rst_n = 0; rx_rst_n = 0;
        paddr = 0; psel = 0; penable = 0; pwrite = 0; pwdata = 0;
        s_axis_tvalid = 0; s_axis_tdata = 0; s_axis_tlast = 0;
        m_axis_tready = 0; rx_serial_in = 0;
        train_start = 0; errors = 0;
        repeat (4) @(posedge rx_clk);
        presetn = 1; s_axis_aresetn = 1; m_axis_aresetn = 1;
        tx_rst_n = 1; rx_rst_n = 1;
        repeat (4) @(posedge pclk);
        apb_write(16'h0000, 32'h0000_0003);
        repeat (8) @(posedge rx_clk);
        @(negedge rx_clk); train_start = 1;
        @(negedge rx_clk); train_start = 0;
        repeat (120) @(posedge rx_clk);
        if (!train_locked || train_best_phase != BEST[1:0]) begin
            $display("ERROR: training not locked locked=%0d best=%0d", train_locked, train_best_phase);
            errors++;
        end
        apb_read(16'h0038, v);
        if (!v[0] || v[3 +: 2] != train_phase || v[5 +: 2] != train_best_phase) begin
            $display("ERROR: training CSR14=%08h phase=%0d best=%0d locked=%0d",
                     v, train_phase, train_best_phase, train_locked);
            errors++;
        end
        apb_read(16'h0034, v);
        if (v !== 32'(train_best_errors)) begin
            $display("ERROR: training CSR13=%08h best_errors=%0d", v, train_best_errors);
            errors++;
        end
        if (errors) $display("FAIL soc training csr errors=%0d", errors);
        else $display("PASS soc training csr phase=%0d best=%0d errors=%0d", train_phase, train_best_phase, train_best_errors);
        $finish;
    end

    initial begin
        #50000; $display("FAIL timeout"); $finish;
    end
endmodule
