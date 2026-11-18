`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// serdes_soc_cdc_wrapper with ENC_8B10B=1.
//
// Serial loopback, two+ consecutive frames.  Verifies that:
//   - the AXIS frame still enters as comma (0xBC) + WORDS payload bytes,
//   - the RX AXIS side emits exactly WORDS payload bytes (the comma symbol is
//     consumed by the align cycle and never forwarded),
//   - the running disparity is reseeded from the comma polarity every frame,
//     so the second and third frames decode bit-exactly (the frame-1 state is
//     not just lucky),
//   - a payload containing 0x00/0xFF/0xAA/0x55 (maximum disparity stress)
//     survives the codec,
//   - no encoder/decoder/disparity error and no frame overflow is reported.
// -----------------------------------------------------------------------------
module tb_soc_cdc_8b10b;

    localparam int DW    = 8;
    localparam int WORDS = 4;

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
    logic code_error;

    int errors;
    int frames;

    always #5  pclk        = ~pclk;
    always #5  s_axis_aclk = ~s_axis_aclk;
    always #7  m_axis_aclk = ~m_axis_aclk;
    always #20 tx_clk      = ~tx_clk;
    always #20 rx_clk      = ~rx_clk;

    // Source-synchronous loopback: the RX samples on the following edge.
    assign rx_serial_in = tx_serial_out;

    serdes_soc_cdc_wrapper #(
        .DATA_WIDTH(DW), .WORDS(WORDS), .LSB_FIRST(1'b1),
        .SYNC_LEN(8), .SYNC_PATTERN(8'hBC), .FIFO_DEPTH(16),
        .CDR_MODE(2), .OVERSAMPLE(1),
        .CRC_ENABLE(1'b0),
        .ENC_8B10B(1'b1)
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
        .code_error(code_error)
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

    task automatic apb_read(input logic [15:0] addr, output logic [31:0] data);
        begin
            @(negedge pclk);
            psel = 1'b1; penable = 1'b0; pwrite = 1'b0;
            paddr = addr;
            @(posedge pclk);
            @(negedge pclk);
            penable = 1'b1;
            #1;
            data = prdata;
            @(posedge pclk);
            @(negedge pclk);
            psel = 1'b0; penable = 1'b0;
        end
    endtask

    task automatic send_beat(input logic [DW-1:0] data, input logic last);
        begin
            @(negedge s_axis_aclk);
            s_axis_tvalid = 1'b1;
            s_axis_tdata  = data;
            s_axis_tlast  = last;
            while (!s_axis_tready) @(negedge s_axis_aclk);
            @(posedge s_axis_aclk);
            #1;
            s_axis_tvalid = 1'b0;
            s_axis_tlast  = 1'b0;
        end
    endtask

    task automatic send_frame(input logic [WORDS-1:0][DW-1:0] pay);
        begin
            send_beat(8'hBC, 1'b0);           // K28.5 comma
            for (int w = 0; w < WORDS; w++)
                send_beat(pay[w], (w == WORDS-1));
        end
    endtask

    task automatic read_beat(output logic [DW-1:0] data, output logic last);
        begin
            @(negedge m_axis_aclk);
            while (!m_axis_tvalid) @(negedge m_axis_aclk);
            data = m_axis_tdata;
            last = m_axis_tlast;
            m_axis_tready = 1'b1;
            @(posedge m_axis_aclk);
            #1;
            m_axis_tready = 1'b0;
        end
    endtask

    task automatic expect_frame(input logic [WORDS-1:0][DW-1:0] pay);
        logic [DW-1:0] data;
        logic last;
        begin
            for (int w = 0; w < WORDS; w++) begin
                read_beat(data, last);
                if (data !== pay[w]) begin
                    $display("ERROR: frame %0d word %0d got %02h expected %02h",
                             frames, w, data, pay[w]);
                    errors++;
                end
                if (last !== (w == WORDS-1)) begin
                    $display("ERROR: frame %0d tlast wrong at word %0d", frames, w);
                    errors++;
                end
            end
            frames++;
        end
    endtask

    // Watchdog: a stuck handshake must fail, not hang the regression.
    initial begin
        #2000000;
        $display("FAIL: tb_soc_cdc_8b10b timeout frames=%0d errors=%0d", frames, errors);
        $finish;
    end

    initial begin
        logic [31:0] status;
        logic [WORDS-1:0][DW-1:0] p1, p2, p3;
        pclk           = 1'b0;
        s_axis_aclk    = 1'b0;
        m_axis_aclk    = 1'b0;
        tx_clk         = 1'b0;
        rx_clk         = 1'b0;
        presetn        = 1'b0;
        s_axis_aresetn = 1'b0;
        m_axis_aresetn = 1'b0;
        tx_rst_n       = 1'b0;
        rx_rst_n       = 1'b0;
        paddr          = '0;
        psel           = 1'b0;
        penable        = 1'b0;
        pwrite         = 1'b0;
        pwdata         = '0;
        s_axis_tvalid  = 1'b0;
        s_axis_tdata   = '0;
        s_axis_tlast   = 1'b0;
        m_axis_tready  = 1'b0;
        errors         = 0;
        frames         = 0;

        p1[0] = 8'h11; p1[1] = 8'h33; p1[2] = 8'h44; p1[3] = 8'h55;
        p2[0] = 8'hFF; p2[1] = 8'h00; p2[2] = 8'hAA; p2[3] = 8'h55;
        p3[0] = 8'h00; p3[1] = 8'h00; p3[2] = 8'h00; p3[3] = 8'h00;

        repeat (4) @(posedge tx_clk);
        presetn        = 1'b1;
        s_axis_aresetn = 1'b1;
        m_axis_aresetn = 1'b1;
        tx_rst_n       = 1'b1;
        rx_rst_n       = 1'b1;
        repeat (4) @(posedge pclk);
        repeat (8) @(posedge tx_clk);

        apb_write(16'h0000, 32'h0000_0003);   // tx_enable | rx_enable
        repeat (16) @(posedge rx_clk);

        // Frame 1 establishes the comma/K28.5 lock.
        send_frame(p1);
        expect_frame(p1);

        // Frames 2 and 3 prove the disparity is reseeded per frame; p2 is the
        // worst case for 8b/10b disparity.
        send_frame(p2);
        expect_frame(p2);
        send_frame(p3);
        expect_frame(p3);

        repeat (8) @(posedge pclk);
        apb_read(16'h001C, status);
        if (!status[4]) begin
            $display("ERROR: rx_lock low status=%h", status);
            errors++;
        end
        if (status[3]) begin
            $display("ERROR: tx_frame_error high status=%h", status);
            errors++;
        end
        if (status[7]) begin
            $display("ERROR: rx_frame_overflow high status=%h", status);
            errors++;
        end
        if (code_error !== 1'b0) begin
            $display("ERROR: 8b/10b code_error sticky high");
            errors++;
        end

        if (errors == 0)
            $display("PASS: tb_soc_cdc_8b10b frames=%0d", frames);
        else
            $display("FAIL: tb_soc_cdc_8b10b errors=%0d", errors);
        $finish;
    end

endmodule
