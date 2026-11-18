`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// serdes_soc_cdc_wrapper with PKT_ENABLE=1: variable-length SoC traffic.
//
// This is the protocol adaptation layer the wrapper needs.  Without it the
// AXIS port must present exactly WORDS beats per frame (the axis_tx_adapter
// contract) and the link carries comma + WORDS payload words.  With
// PKT_ENABLE=1 the port takes frames of *any* length -- Ethernet-shaped, AXI
// burst-shaped, 1 byte, exactly WORDS, multi-fragment -- and the packetizer /
// depacketizer slice them into the fixed internal frames.
//
// Checks:
//   1. Frame lengths 1, WORDS-1, WORDS, 2*WORDS+3 and 17 round-trip
//      byte-exactly through the serial loopback, with tlast exactly at the
//      original frame end (the depacketizer reconstructs it from the fragment
//      header, not from a sideband).
//   2. Each frame's payload is distinct and never a reserved ordered set, so a
//      misordered or duplicated frame cannot pass by luck.
//   3. No pkt_tx_frame_error / pkt_rx_frame_error, no code_error.
//   4. Back-to-back frames work: the packetizer never interleaves two input
//      frames.
// -----------------------------------------------------------------------------
module tb_soc_cdc_packetizer;

    localparam int DW    = 8;
    localparam int WORDS = 4;
    localparam int NLEN  = 5;
    localparam int MAXB  = 512;

    logic pclk, presetn;
    logic [15:0] paddr;
    logic psel, penable, pwrite;
    logic [31:0] pwdata, prdata;
    logic pready, pslverr;

    logic s_axis_aclk, s_axis_aresetn;
    logic s_axis_tvalid, s_axis_tready;
    logic [DW-1:0] s_axis_tdata;
    logic s_axis_tlast;

    logic m_axis_aclk, m_axis_aresetn;
    logic m_axis_tvalid, m_axis_tready;
    logic [DW-1:0] m_axis_tdata;
    logic m_axis_tlast;

    logic tx_clk, tx_rst_n, tx_serial_out, tx_serial_valid;
    logic rx_clk, rx_rst_n, rx_serial_in;
    logic rx_sample_strobe, rx_serial_data, rx_serial_valid;
    logic signed [12:0] rx_freq_trim;
    logic rx_clk_out;
    logic [19:0][31:0] csr_regs;
    logic code_error, pkt_tx_frame_error, pkt_rx_frame_error;

    int errors;
    int tx_frames, rx_frames;

    always #5  pclk        = ~pclk;
    always #5  s_axis_aclk = ~s_axis_aclk;
    always #7  m_axis_aclk = ~m_axis_aclk;
    always #20 tx_clk      = ~tx_clk;
    always #20 rx_clk      = ~rx_clk;

    assign rx_serial_in = tx_serial_out;   // source-synchronous loopback

    serdes_soc_cdc_wrapper #(
        .DATA_WIDTH(DW), .WORDS(WORDS), .LSB_FIRST(1'b1),
        .SYNC_LEN(8), .SYNC_PATTERN(8'hBC), .FIFO_DEPTH(16),
        .CDR_MODE(2), .OVERSAMPLE(1),
        .CRC_ENABLE(1'b0),
        .ENC_8B10B(1'b0),
        .PKT_ENABLE(1'b1)
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
        .train_start(1'b0), .train_error_count('0),
        .code_error(code_error),
        .pkt_tx_frame_error(pkt_tx_frame_error),
        .pkt_rx_frame_error(pkt_rx_frame_error)
    );

    task automatic apb_write(input logic [15:0] addr, input logic [31:0] data);
        begin
            @(negedge pclk);
            psel = 1'b1; penable = 1'b0; pwrite = 1'b1;
            paddr = addr; pwdata = data;
            @(posedge pclk);
            @(negedge pclk);
            penable = 1'b1;
            @(posedge pclk);
            #1;
            @(negedge pclk);
            psel = 1'b0; penable = 1'b0; pwrite = 1'b0;
        end
    endtask

    // One variable-length frame: `n` bytes, tlast on the last one.
    // Each frame's payload is `base + k`, and both the stimulus and the
    // scoreboard use this function, so the two cannot disagree.
    function automatic logic [DW-1:0] frame_byte(input int f, input int k);
        frame_byte = DW'(((f * 43) + k) & 8'h3F) | 8'h40;
    endfunction

    task automatic send_frame(input int f, input int n);
        begin
            for (int k = 0; k < n; k++) begin
                @(negedge s_axis_aclk);
                s_axis_tvalid = 1'b1;
                s_axis_tdata  = frame_byte(f, k);
                s_axis_tlast  = (k == n - 1);
                while (!s_axis_tready) @(negedge s_axis_aclk);
                @(posedge s_axis_aclk);
                #1;
                s_axis_tvalid = 1'b0;
                s_axis_tlast  = 1'b0;
            end
            tx_frames = tx_frames + 1;
        end
    endtask

    // Expected byte stream.
    logic [DW-1:0] exp_data [MAXB];
    logic          exp_last [MAXB];
    int            exp_n;

    int lens [NLEN];

    initial begin
        lens[0] = 1;
        lens[1] = WORDS - 1;
        lens[2] = WORDS;
        lens[3] = 2 * WORDS + 3;
        lens[4] = 17;
        exp_n = 0;
        for (int f = 0; f < NLEN; f++)
            for (int k = 0; k < lens[f]; k++) begin
                exp_data[exp_n] = frame_byte(f, k);
                exp_last[exp_n] = (k == lens[f] - 1);
                exp_n = exp_n + 1;
            end
    end

    int obeat;
    always @(posedge m_axis_aclk) begin
        if (!m_axis_aresetn) begin
            obeat <= 0;
            rx_frames <= 0;
        end else if (m_axis_tvalid && m_axis_tready) begin
            if (obeat >= exp_n) begin
                $display("ERROR: extra output byte %02h", m_axis_tdata);
                errors <= errors + 1;
            end else begin
                if (m_axis_tdata !== exp_data[obeat]) begin
                    $display("ERROR: byte %0d got %02h expected %02h",
                             obeat, m_axis_tdata, exp_data[obeat]);
                    errors <= errors + 1;
                end
                if (m_axis_tlast !== exp_last[obeat]) begin
                    $display("ERROR: tlast wrong at byte %0d", obeat);
                    errors <= errors + 1;
                end
            end
            if (m_axis_tlast)
                rx_frames <= rx_frames + 1;
            obeat <= obeat + 1;
        end
    end

    initial begin
        errors = 0; tx_frames = 0; rx_frames = 0;
        pclk = 0; s_axis_aclk = 0; m_axis_aclk = 0;
        tx_clk = 0; rx_clk = 0;
        presetn = 0; s_axis_aresetn = 0; m_axis_aresetn = 0;
        tx_rst_n = 0; rx_rst_n = 0;
        psel = 0; penable = 0; pwrite = 0; paddr = 0; pwdata = 0;
        s_axis_tvalid = 0; s_axis_tdata = 0; s_axis_tlast = 0;
        m_axis_tready = 0;

        repeat (6) @(posedge pclk);
        presetn        = 1'b1;
        s_axis_aresetn = 1'b1;
        m_axis_aresetn = 1'b1;
        tx_rst_n       = 1'b1;
        rx_rst_n       = 1'b1;
        repeat (6) @(posedge pclk);

        // Bring the link up through the APB control plane (CSR0).
        apb_write(16'h0000, 32'h0000_0003);   // tx_enable | rx_enable
        repeat (20) @(posedge pclk);

        m_axis_tready = 1'b1;

        // Five variable-length frames, back to back.
        for (int f = 0; f < NLEN; f++)
            send_frame(f, lens[f]);

        for (int t = 0; t < 20000 && obeat < exp_n; t++)
            @(posedge m_axis_aclk);
        repeat (60) @(posedge m_axis_aclk);

        if (obeat != exp_n) begin
            $display("ERROR: output bytes %0d expected %0d", obeat, exp_n);
            errors = errors + 1;
        end
        if (rx_frames != NLEN) begin
            $display("ERROR: output frames %0d expected %0d", rx_frames, NLEN);
            errors = errors + 1;
        end
        if (pkt_tx_frame_error !== 1'b0) begin
            $display("ERROR: pkt_tx_frame_error high");
            errors = errors + 1;
        end
        if (pkt_rx_frame_error !== 1'b0) begin
            $display("ERROR: pkt_rx_frame_error high");
            errors = errors + 1;
        end
        if (code_error !== 1'b0) begin
            $display("ERROR: code_error high");
            errors = errors + 1;
        end

        if (errors == 0)
            $display("PASS: tb_soc_cdc_packetizer frames=%0d bytes=%0d frags=ok",
                     rx_frames, obeat);
        else
            $display("FAIL: tb_soc_cdc_packetizer errors=%0d bytes=%0d/%0d frames=%0d/%0d",
                     errors, obeat, exp_n, rx_frames, NLEN);
        $finish;
    end

    initial begin
        #4000000;
        $display("FAIL: tb_soc_cdc_packetizer timeout bytes=%0d/%0d errors=%0d",
                 obeat, exp_n, errors);
        $finish;
    end

endmodule
