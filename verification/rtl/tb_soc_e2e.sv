`timescale 1ns/1ps
module tb_soc_e2e #(
    parameter int DATA_WIDTH = 8,
    parameter int WORDS = 4,
    parameter int SYNC_LEN = 8,
    parameter logic [SYNC_LEN-1:0] SYNC_PATTERN = 'hBC,
    parameter int N_FRAMES = 4,
    parameter int BIST_PRBS_WIDTH = 7,
    parameter int RX_DELAY_PS = 5000
);
    logic pclk, presetn;
    logic s_axis_aclk, s_axis_aresetn;
    logic m_axis_aclk, m_axis_aresetn;
    logic tx_clk, tx_rst_n;
    logic rx_clk;
    logic psel, penable, pwrite;
    logic [15:0] paddr;
    logic [31:0] pwdata, prdata;
    logic pready, pslverr;
    logic s_axis_tvalid, s_axis_tready, s_axis_tlast;
    logic [DATA_WIDTH-1:0] s_axis_tdata;
    logic m_axis_tvalid, m_axis_tready, m_axis_tlast;
    logic [DATA_WIDTH-1:0] m_axis_tdata;
    logic tx_serial_out, tx_serial_valid, rx_serial_in;
    logic rx_sample_strobe, rx_serial_data, rx_serial_valid, rx_clk_out;
    logic signed [12:0] rx_freq_trim;
    logic [511:0] csr_regs;

    logic [DATA_WIDTH-1:0] expected [0:N_FRAMES-1][0:WORDS-1];
    logic [DATA_WIDTH-1:0] got [0:WORDS-1];
    int frame_idx, word_idx, out_frames, normal_frames, errors, apb_errors;
    logic scoreboard_enable;
    int bp_mode;
    int bp_count;
    logic [31:0] status, bist_status, bist_errors, bist_bits;
    logic [31:0] scratch;

    assign rx_serial_in = tx_serial_out;

    serdes_soc_cdc_wrapper #(
        .DATA_WIDTH(DATA_WIDTH), .WORDS(WORDS), .LSB_FIRST(1'b1),
        .SYNC_LEN(SYNC_LEN), .SYNC_PATTERN(SYNC_PATTERN),
        .BIST_PRBS_WIDTH(BIST_PRBS_WIDTH)
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
        .tx_clk(tx_clk), .tx_rst_n(tx_rst_n),
        .tx_serial_out(tx_serial_out), .tx_serial_valid(tx_serial_valid),
        .rx_clk(rx_clk), .rx_rst_n(tx_rst_n), .rx_serial_in(rx_serial_in),
        .rx_sample_strobe(rx_sample_strobe), .rx_serial_data(rx_serial_data),
        .rx_serial_valid(rx_serial_valid), .rx_freq_trim(rx_freq_trim),
        .rx_clk_out(rx_clk_out), .csr_regs(csr_regs)
    );

    initial begin tx_clk = 0; forever #5 tx_clk = ~tx_clk; end       // 100 MHz
    // Forwarded-clock model: fixed phase offset relative to the TX bit clock.
    // 5 ns places the sampling edge at the center of the 10 ns bit.
    initial begin rx_clk = 0; #(RX_DELAY_PS / 1000.0);
        forever #5 rx_clk = ~rx_clk; end
    initial begin s_axis_aclk = 0; forever #10 s_axis_aclk = ~s_axis_aclk; end // 50 MHz
    initial begin m_axis_aclk = 0; forever #12.5 m_axis_aclk = ~m_axis_aclk; end // 40 MHz
    initial begin pclk = 0; forever #10 pclk = ~pclk; end             // 50 MHz

    always @(negedge m_axis_aclk) begin
        bp_count <= bp_count + 1;
        if (bp_mode == 0) m_axis_tready <= 1'b1;
        else if (bp_mode == 1) m_axis_tready <= ((bp_count % 5) < 2);
        else m_axis_tready <= 1'b0;
    end

    task automatic apb_write(input [15:0] addr, input [31:0] data);
        begin
            @(negedge pclk); psel = 1; pwrite = 1; paddr = addr; pwdata = data; penable = 0;
            @(negedge pclk); penable = 1;
            @(posedge pclk); while (!pready) @(posedge pclk);
            @(negedge pclk); psel = 0; penable = 0; pwrite = 0;
        end
    endtask

    task automatic apb_read(input [15:0] addr, output [31:0] data);
        begin
            @(negedge pclk); psel = 1; pwrite = 0; paddr = addr; penable = 0;
            @(negedge pclk); penable = 1;
            @(posedge pclk); while (!pready) @(posedge pclk); data = prdata;
            @(negedge pclk); psel = 0; penable = 0;
        end
    endtask

    task automatic send_frame(input int frame);
        begin
            for (int i = 0; i < WORDS + 1; i++) begin
                @(negedge s_axis_aclk);
                s_axis_tvalid = 1;
                s_axis_tdata = (i == 0) ? DATA_WIDTH'(SYNC_PATTERN) : expected[frame][i-1];
                s_axis_tlast = (i == WORDS);
                @(posedge s_axis_aclk);
                while (!s_axis_tready) @(posedge s_axis_aclk);
            end
            @(negedge s_axis_aclk); s_axis_tvalid = 0; s_axis_tlast = 0;
        end
    endtask

    always @(posedge m_axis_aclk) begin
        if (scoreboard_enable && m_axis_tvalid && m_axis_tready) begin
            got[word_idx] = m_axis_tdata;
            if (m_axis_tlast) begin
                if (frame_idx < N_FRAMES) begin
                    for (int w = 0; w < WORDS; w++)
                        if (got[w] !== expected[frame_idx][w]) errors = errors + 1;
                    frame_idx = frame_idx + 1;
                    out_frames = out_frames + 1;
                    if (frame_idx >= N_FRAMES) scoreboard_enable = 0;
                end
                word_idx = 0;
            end else if (word_idx < WORDS - 1) begin
                word_idx = word_idx + 1;
            end
        end
    end

    initial begin
        presetn = 0; s_axis_aresetn = 0; m_axis_aresetn = 0; tx_rst_n = 0;
        psel = 0; penable = 0; pwrite = 0; paddr = 0; pwdata = 0;
        s_axis_tvalid = 0; s_axis_tdata = 0; s_axis_tlast = 0;
        m_axis_tready = 0;
        frame_idx = 0; word_idx = 0; out_frames = 0; normal_frames = 0;
        errors = 0; apb_errors = 0; scoreboard_enable = 1;
        bp_mode = 1; bp_count = 0;
        for (int f = 0; f < N_FRAMES; f++)
            for (int w = 0; w < WORDS; w++)
                expected[f][w] = DATA_WIDTH'((f * WORDS + w + 1) & 255);
        repeat (8) @(posedge pclk);
        presetn = 1; s_axis_aresetn = 1; m_axis_aresetn = 1; tx_rst_n = 1;
        repeat (4) @(posedge pclk);
        apb_write(16'h0004, 32'hA5A5_1234);
        apb_read(16'h0004, scratch);
        if (scratch !== 32'hA5A5_1234) apb_errors++;
        apb_write(16'h0000, 32'h0000_0003);
        repeat (20) @(posedge tx_clk);

        for (int f = 0; f < N_FRAMES; f++) send_frame(f);
        repeat (2000) @(posedge m_axis_aclk);
        apb_read(16'h001C, status);
        if (!status[4]) apb_errors++;       // rx_lock
        if (!status[6]) apb_errors++;       // rx_frame_locked
        normal_frames = out_frames;
        if (normal_frames != N_FRAMES) errors = errors + 1;
        scoreboard_enable = 0;

        // BIST PRBS loopback and coherent snapshot.  Start first, allow the
        // PRBS counters to advance, then request a snapshot (req is edge-
        // triggered, not a level that can be left asserted).
        apb_write(16'h0020, 32'h0000_0013); // enable/start/loopback
        repeat (1000) @(posedge tx_clk);
        apb_write(16'h0020, 32'h0000_0033); // request snapshot
        repeat (20) @(posedge pclk);
        apb_read(16'h0030, bist_status);
        apb_read(16'h0028, bist_errors);
        apb_read(16'h002C, bist_bits);
        if (bist_errors != 0 || bist_bits == 0 || bist_status[0]) errors++;
        apb_write(16'h0020, 32'h0000_0000);
        repeat (20) @(posedge tx_clk);

        // Deliberate RX AXIS backpressure/overflow: stop draining, push frames.
        bp_mode = 2;
        for (int f = 0; f < 12; f++) send_frame(f % N_FRAMES);
        repeat (100) @(posedge tx_clk);
        apb_read(16'h001C, status);
        if (!status[7]) apb_errors++;       // rx_frame_overflow
        bp_mode = 0;

        $display("SOC_E2E frames=%0d expected=%0d errors=%0d apb_errors=%0d status=%08x bist_status=%08x bist_errors=%0d bist_bits=%0d",
                 normal_frames, N_FRAMES, errors, apb_errors, status, bist_status, bist_errors, bist_bits);
        if (errors != 0 || apb_errors != 0 || normal_frames != N_FRAMES)
            $display("SOC_E2E FAIL");
        else
            $display("SOC_E2E PASS");
        $finish;
    end

    initial begin
        #2_000_000;
        $display("SOC_E2E TIMEOUT frames=%0d errors=%0d apb_errors=%0d", out_frames, errors, apb_errors);
        $finish;
    end
endmodule
