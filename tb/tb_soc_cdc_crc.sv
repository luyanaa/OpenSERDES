`timescale 1ns/1ps

module tb_soc_cdc_crc;

    localparam int DW    = 8;
    localparam int WORDS = 4;
    localparam int RATIO = DW * WORDS;

    logic pclk;
    logic presetn;
    logic [15:0] paddr;
    logic psel;
    logic penable;
    logic pwrite;
    logic [31:0] pwdata;
    logic pready;
    logic [31:0] prdata;
    logic pslverr;

    logic s_axis_aclk;
    logic s_axis_aresetn;
    logic s_axis_tvalid;
    logic s_axis_tready;
    logic [DW-1:0] s_axis_tdata;
    logic s_axis_tlast;

    logic m_axis_aclk;
    logic m_axis_aresetn;
    logic m_axis_tvalid;
    logic m_axis_tready;
    logic [DW-1:0] m_axis_tdata;
    logic m_axis_tlast;

    logic tx_clk;
    logic tx_rst_n;
    logic tx_serial_out;
    logic tx_serial_valid;
    logic rx_clk;
    logic rx_rst_n;
    logic rx_serial_in;
    logic rx_sample_strobe;
    logic rx_serial_data;
    logic rx_serial_valid;
    logic signed [12:0] rx_freq_trim;
    logic rx_clk_out;
    logic [15:0][31:0] csr_regs;

    int errors;
    int tx_cycles;
    logic [RATIO-1:0] payload;

    // CSR word addresses used by the BIST checks.
    localparam logic [15:0] ADDR_CONTROL   = 16'h0000;
    localparam logic [15:0] ADDR_STATUS    = 16'h001C;
    localparam logic [15:0] ADDR_BIST_CTRL = 16'h0020;
    localparam logic [15:0] ADDR_BIST_ERR  = 16'h0028;
    localparam logic [15:0] ADDR_BIST_BITS = 16'h002C;
    localparam logic [15:0] ADDR_BIST_STAT = 16'h0030;

    always #5 pclk = ~pclk;
    always #5 s_axis_aclk = ~s_axis_aclk;
    always #7 m_axis_aclk = ~m_axis_aclk;
    always #20 tx_clk = ~tx_clk;
    always #20 rx_clk = ~rx_clk;

    always @(posedge tx_clk) tx_cycles++;

    assign rx_serial_in = tx_serial_out;

    serdes_soc_cdc_wrapper #(
        .DATA_WIDTH(DW), .WORDS(WORDS), .LSB_FIRST(1'b1),
        .SYNC_LEN(8), .SYNC_PATTERN(8'hBC), .FIFO_DEPTH(16),
        .BIST_PRBS_WIDTH(7), .BIST_SEED(7'h7F),
        .CRC_ENABLE(1'b1), .CRC_WIDTH(16)
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
        .rx_clk_out(rx_clk_out),
        .train_start(1'b0), .train_error_count('0),
        .csr_regs(csr_regs)
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

    task automatic send_word(input logic [DW-1:0] data, input logic last);
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

    // Pulse BIST_CTRL[5] while keeping 'base' in the rest of the register,
    // wait for the stable-bus snapshot to be acknowledged, then read the
    // coherent counter words.  The helper leaves the snapshot bit low so the
    // next call produces a fresh rising edge.
    task automatic bist_snapshot(input  logic [31:0] base,
                                 output logic [31:0] err_cnt,
                                 output logic [31:0] bit_cnt,
                                 output logic [31:0] bist_status);
        begin
            logic [31:0] word;
            apb_write(ADDR_BIST_CTRL, base | 32'h0000_0020);
            apb_write(ADDR_BIST_CTRL, base & ~32'h0000_0020);
            word = '0;
            for (int i = 0; i < 64 && !word[2]; i++)
                apb_read(ADDR_BIST_STAT, word);
            if (!word[2]) begin
                $display("ERROR: BIST snapshot valid not set after 64 reads");
                errors++;
            end
            apb_read(ADDR_BIST_ERR,  err_cnt);
            apb_read(ADDR_BIST_BITS, bit_cnt);
            apb_read(ADDR_BIST_STAT, bist_status);
        end
    endtask

    initial begin
        logic [31:0] status;
        logic [DW-1:0] data;
        logic last;
        logic [31:0] err_cnt, bit_cnt, bist_status;
        int b0, c0, d_bits, d_cyc;
        pclk            = 1'b0;
        s_axis_aclk     = 1'b0;
        m_axis_aclk     = 1'b0;
        tx_clk          = 1'b0;
        rx_clk          = 1'b0;
        presetn         = 1'b0;
        s_axis_aresetn  = 1'b0;
        m_axis_aresetn  = 1'b0;
        tx_rst_n        = 1'b0;
        rx_rst_n        = 1'b0;
        paddr           = '0;
        psel            = 1'b0;
        penable         = 1'b0;
        pwrite          = 1'b0;
        pwdata          = '0;
        s_axis_tvalid   = 1'b0;
        s_axis_tdata    = '0;
        s_axis_tlast    = 1'b0;
        m_axis_tready   = 1'b0;
        errors          = 0;
        tx_cycles       = 0;
        payload         = 32'h89AB_CDEF;

        repeat (4) @(posedge tx_clk);
        presetn        = 1'b1;
        s_axis_aresetn = 1'b1;
        m_axis_aresetn = 1'b1;
        tx_rst_n       = 1'b1;
        rx_rst_n       = 1'b1;
        repeat (4) @(posedge pclk);
        repeat (4) @(posedge tx_clk);

        apb_write(16'h0000, 32'h0000_0003); // tx_enable | rx_enable
        apb_write(16'h0004, 32'hCAFE_BABE);
        apb_read (16'h0004, status);
        if (status !== 32'hCAFE_BABE) begin
            $display("ERROR: APB scratch read %h", status);
            errors++;
        end
        repeat (8) @(posedge tx_clk);
        repeat (8) @(posedge rx_clk);

        send_word(8'hBC, 1'b0);
        for (int w = 0; w < WORDS; w++)
            send_word(payload[w*DW +: DW], (w == WORDS-1));

        for (int w = 0; w < WORDS; w++) begin
            read_beat(data, last);
            if (data !== payload[w*DW +: DW]) begin
                $display("ERROR: RX AXIS word %0d got %h expected %h",
                         w, data, payload[w*DW +: DW]);
                errors++;
            end
            if (last !== (w == WORDS-1)) begin
                $display("ERROR: RX AXIS tlast wrong at word %0d", w);
                errors++;
            end
        end

        repeat (8) @(posedge pclk);
        apb_read(ADDR_STATUS, status);
        if (!status[4]) begin
            $display("ERROR: status rx_lock low");
            errors++;
        end
        if (!status[6]) begin
            $display("ERROR: status rx_frame_locked low");
            errors++;
        end
        if (status[7]) begin
            $display("ERROR: status rx_frame_overflow high");
            errors++;
        end
        if (status[3]) begin
            $display("ERROR: status tx_frame_error high");
            errors++;
        end

        // ------------------------------------------------------------------
        // PRBS BIST: loopback self-check, coherent counters, injection, clear.
        // ------------------------------------------------------------------
        apb_write(ADDR_CONTROL, 32'h0000_0000); // park the AXIS datapath

        // a. PRBS7 loopback: enable|start|clear|loopback, then release clear.
        apb_write(ADDR_BIST_CTRL, 32'h0000_0017);
        apb_write(ADDR_BIST_CTRL, 32'h0000_0013);
        repeat (12) @(posedge tx_clk);          // cdc_sync latency
        repeat (400) @(posedge tx_clk);         // let the counters run

        // b. First snapshot: running, self-checking, PRBS7 selected.
        bist_snapshot(32'h0000_0013, err_cnt, bit_cnt, bist_status);
        if (!bist_status[2]) begin
            $display("ERROR: BIST snap_valid low status=%h", bist_status);
            errors++;
        end
        if (bist_status[0]) begin
            $display("ERROR: BIST error sticky high in loopback");
            errors++;
        end
        if (!bist_status[1]) begin
            $display("ERROR: BIST running low while start&enable");
            errors++;
        end
        if (bist_status[7:5] !== 3'b001) begin
            $display("ERROR: BIST width code %b expected 001", bist_status[7:5]);
            errors++;
        end
        if (bist_status[3] !== 1'b1) begin
            $display("ERROR: BIST loopback live bit low");
            errors++;
        end
        if (err_cnt != 32'd0) begin
            $display("ERROR: BIST error_count %0d expected 0", err_cnt);
            errors++;
        end
        $display("INFO: BIST snapshot bits=%0d errors=%0d status=%h",
                 bit_cnt, err_cnt, bist_status);

        // c. Two snapshots 400 TX clocks apart: bit_count must track tx_clk.
        bist_snapshot(32'h0000_0013, err_cnt, bit_cnt, bist_status);
        b0 = int'(bit_cnt);
        c0 = tx_cycles;
        repeat (400) @(posedge tx_clk);
        bist_snapshot(32'h0000_0013, err_cnt, bit_cnt, bist_status);
        d_bits = int'(bit_cnt) - b0;
        d_cyc  = tx_cycles - c0;
        if (d_bits < d_cyc - 8 || d_bits > d_cyc + 8) begin
            $display("ERROR: BIST bit delta %0d vs tx delta %0d", d_bits, d_cyc);
            errors++;
        end

        // d. Error injection: XOR the checker input for a few TX clocks.
        apb_write(ADDR_BIST_CTRL, 32'h0000_001B); // enable|start|inject|loopback
        repeat (4) @(posedge tx_clk);
        apb_write(ADDR_BIST_CTRL, 32'h0000_0013); // stop injecting
        repeat (4) @(posedge tx_clk);
        bist_snapshot(32'h0000_0013, err_cnt, bit_cnt, bist_status);
        if (bist_status[0] !== 1'b1) begin
            $display("ERROR: BIST error sticky low after injection");
            errors++;
        end
        if (err_cnt < 32'd1) begin
            $display("ERROR: BIST error_count %0d expected >=1", err_cnt);
            errors++;
        end

        // e. Clear while paused: sticky error and counters snap to zero.
        apb_write(ADDR_BIST_CTRL, 32'h0000_0014); // clear|loopback, BIST paused
        repeat (4) @(posedge tx_clk);             // let clear reach tx_clk
        bist_snapshot(32'h0000_0014, err_cnt, bit_cnt, bist_status);
        if (bist_status[0] !== 1'b0) begin
            $display("ERROR: BIST error sticky high after clear");
            errors++;
        end
        if (err_cnt != 32'd0) begin
            $display("ERROR: BIST error_count %0d expected 0 after clear", err_cnt);
            errors++;
        end
        if (bit_cnt != 32'd0) begin
            $display("ERROR: BIST bit_count %0d expected 0 after clear", bit_cnt);
            errors++;
        end

        if (errors != 0) begin
            $display("FAIL: tb_serdes_soc_cdc_wrapper errors=%0d", errors);
            $fatal(1);
        end
        $display("PASS: tb_serdes_soc_cdc_wrapper");
        $finish;
    end

endmodule
