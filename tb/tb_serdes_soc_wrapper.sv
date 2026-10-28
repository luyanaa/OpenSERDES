`timescale 1ns/1ps

module tb_serdes_soc_wrapper;

    localparam int DW    = 8;
    localparam int WORDS = 4;
    localparam int RATIO = DW * WORDS;

    logic clk;
    logic rst_n;
    logic [15:0] paddr;
    logic psel;
    logic penable;
    logic pwrite;
    logic [31:0] pwdata;
    logic pready;
    logic [31:0] prdata;
    logic pslverr;

    logic s_axis_tvalid;
    logic s_axis_tready;
    logic [DW-1:0] s_axis_tdata;
    logic s_axis_tlast;
    logic m_axis_tvalid;
    logic m_axis_tready;
    logic [DW-1:0] m_axis_tdata;
    logic m_axis_tlast;

    logic tx_serial_out;
    logic tx_serial_valid;
    logic rx_serial_in;
    logic rx_sample_strobe;
    logic rx_serial_data;
    logic rx_serial_valid;
    logic signed [12:0] rx_freq_trim;
    logic rx_clk_out;
    logic [15:0][31:0] csr_regs;

    int errors;
    int cyc;
    logic [RATIO-1:0] payload;

    always #5 clk = ~clk;

    always @(posedge clk) cyc++;

    logic inject_bit;
    assign rx_serial_in = tx_serial_out ^ inject_bit;

    serdes_soc_wrapper #(
        .DATA_WIDTH(DW), .WORDS(WORDS), .LSB_FIRST(1'b1),
        .SYNC_LEN(8), .SYNC_PATTERN(8'hBC)
    ) dut (
        .clk(clk), .rst_n(rst_n),
        .paddr(paddr), .psel(psel), .penable(penable), .pwrite(pwrite),
        .pwdata(pwdata), .pready(pready), .prdata(prdata), .pslverr(pslverr),
        .s_axis_tvalid(s_axis_tvalid), .s_axis_tready(s_axis_tready),
        .s_axis_tdata(s_axis_tdata), .s_axis_tlast(s_axis_tlast),
        .m_axis_tvalid(m_axis_tvalid), .m_axis_tready(m_axis_tready),
        .m_axis_tdata(m_axis_tdata), .m_axis_tlast(m_axis_tlast),
        .tx_serial_out(tx_serial_out), .tx_serial_valid(tx_serial_valid),
        .rx_serial_in(rx_serial_in), .rx_sample_strobe(rx_sample_strobe),
        .rx_serial_data(rx_serial_data), .rx_serial_valid(rx_serial_valid),
        .rx_freq_trim(rx_freq_trim), .rx_clk_out(rx_clk_out),
        .csr_regs(csr_regs)
    );

    task automatic apb_write(input logic [15:0] addr, input logic [31:0] data);
        begin
            @(negedge clk);
            psel = 1'b1; penable = 1'b0; pwrite = 1'b1;
            paddr = addr; pwdata = data;
            @(posedge clk);
            @(negedge clk);
            penable = 1'b1;
            @(posedge clk);
            #1;
            @(negedge clk);
            psel = 1'b0; penable = 1'b0; pwrite = 1'b0;
        end
    endtask

    task automatic apb_read(input logic [15:0] addr, output logic [31:0] data);
        begin
            @(negedge clk);
            psel = 1'b1; penable = 1'b0; pwrite = 1'b0;
            paddr = addr;
            @(posedge clk);
            @(negedge clk);
            penable = 1'b1;
            #1;
            data = prdata;
            @(posedge clk);
            @(negedge clk);
            psel = 1'b0; penable = 1'b0;
        end
    endtask

    task automatic send_word(input logic [DW-1:0] data, input logic last);
        begin
            @(negedge clk);
            s_axis_tvalid = 1'b1;
            s_axis_tdata  = data;
            s_axis_tlast  = last;
            while (!s_axis_tready) @(negedge clk);
            @(posedge clk);
            #1;
            s_axis_tvalid = 1'b0;
            s_axis_tlast  = 1'b0;
        end
    endtask

    task automatic read_beat(output logic [DW-1:0] data, output logic last);
        begin
            @(negedge clk);
            while (!m_axis_tvalid) @(negedge clk);
            data = m_axis_tdata;
            last = m_axis_tlast;
            m_axis_tready = 1'b1;
            @(posedge clk);
            #1;
            m_axis_tready = 1'b0;
        end
    endtask

    // Poll BIST_STATUS.snap_valid and read the coherent snapshot words.
    // ok is low when the snapshot did not become valid within 64 reads.
    task automatic bist_wait_snapshot(
        output logic        ok,
        output logic [31:0] error_count,
        output logic [31:0] bit_count,
        output logic [31:0] bist_status
    );
        logic [31:0] rd;
        logic        seen;
        begin
            rd   = '0;
            seen = 1'b0;
            for (int i = 0; i < 64 && !seen; i = i + 1) begin
                apb_read(16'h0030, rd);
                seen = rd[2];
            end
            ok = seen;
            apb_read(16'h0028, error_count);
            apb_read(16'h002C, bit_count);
            apb_read(16'h0030, bist_status);
        end
    endtask

    // Request a snapshot (BIST_CTRL[5] 0->1 then back to 0) and return the
    // captured words.  base holds the rest of BIST_CTRL; for the running BIST
    // this writes 0x33 then 0x13.  stamp is taken at a fixed offset from the
    // request commit so a bit_count delta can be compared with cycles.
    task automatic bist_snapshot(
        input  logic [31:0] base,
        output logic [31:0] error_count,
        output logic [31:0] bit_count,
        output logic [31:0] bist_status,
        output int          stamp
    );
        logic        ok;
        logic [31:0] ecnt;
        logic [31:0] bcnt;
        logic [31:0] bstat;
        begin
            ok    = 1'b0;
            ecnt  = '0;
            bcnt  = '0;
            bstat = '0;
            apb_write(16'h0020, base | 32'h0000_0020);
            stamp = cyc;
            apb_write(16'h0020, base);
            bist_wait_snapshot(ok, ecnt, bcnt, bstat);
            if (!ok) begin
                $display("ERROR: BIST snapshot valid did not assert within 64 reads");
                errors++;
            end
            error_count = ecnt;
            bit_count   = bcnt;
            bist_status = bstat;
        end
    endtask

    initial begin
        logic [31:0] status;
        logic [DW-1:0] data;
        logic last;
        logic [31:0] ecnt;
        logic [31:0] bcnt;
        logic [31:0] bc1;
        logic [31:0] bstat;
        logic        snap_ok;
        int          c0;
        int          c1;
        int          dcyc;
        int          dbits;
        int          stamp;
        clk           = 1'b0;
        rst_n         = 1'b0;
        paddr         = '0;
        psel          = 1'b0;
        penable       = 1'b0;
        pwrite        = 1'b0;
        pwdata        = '0;
        s_axis_tvalid = 1'b0;
        s_axis_tdata  = '0;
        s_axis_tlast  = 1'b0;
        m_axis_tready = 1'b0;
        inject_bit    = 1'b0;
        errors        = 0;
        cyc           = 0;
        payload       = 32'h0123_4567;

        repeat (3) @(posedge clk);
        rst_n = 1'b1;
        repeat (3) @(posedge clk);

        apb_write(16'h0000, 32'h0000_0003); // tx_enable | rx_enable
        apb_write(16'h0004, 32'hCAFE_BABE); // scratch
        apb_read (16'h0004, status);
        if (status !== 32'hCAFE_BABE) begin
            $display("ERROR: APB scratch read %h", status);
            errors++;
        end

        // TX frame: comma + 4 payload words.
        send_word(8'hBC, 1'b0);
        for (int w = 0; w < WORDS; w++)
            send_word(payload[w*DW +: DW], (w == WORDS-1));

        // RX frame: 4 payload words.
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

        apb_read(16'h001C, status);
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
        // BIST / loopback tests
        // ------------------------------------------------------------------
        // Disable normal traffic; the BIST owns the serial line from here.
        apb_write(16'h0000, 32'h0000_0000);
        apb_write(16'h0020, 32'h0000_0017); // enable|start|clear|loopback
        apb_write(16'h0020, 32'h0000_0013); // drop clear, BIST runs
        repeat (300) @(posedge clk);

        // b) Snapshot of the healthy PRBS7 link.
        bist_snapshot(32'h0000_0013, ecnt, bcnt, bstat, c0);
        if (!bstat[2]) begin
            $display("ERROR: BIST snapshot valid not set");
            errors++;
        end
        if (bstat[0]) begin
            $display("ERROR: BIST error flag set on clean loopback");
            errors++;
        end
        if (!bstat[1]) begin
            $display("ERROR: BIST running flag low while start&enable");
            errors++;
        end
        if (bstat[7:5] !== 3'b001) begin
            $display("ERROR: BIST width code %b expected 3'b001 (PRBS7)", bstat[7:5]);
            errors++;
        end
        if (ecnt !== 32'd0) begin
            $display("ERROR: BIST error_count %0d expected 0", ecnt);
            errors++;
        end

        // c) bit_count must track the free-running clock.
        bist_snapshot(32'h0000_0013, ecnt, bcnt, bstat, c0);
        repeat (500) @(posedge clk);
        bist_snapshot(32'h0000_0013, ecnt, bc1, bstat, c1);
        dcyc  = c1 - c0;
        dbits = int'(bc1) - int'(bcnt);
        if (dbits < dcyc - 8 || dbits > dcyc + 8) begin
            $display("ERROR: BIST bit_count delta %0d vs cycle delta %0d",
                     dbits, dcyc);
            errors++;
        end

        // d) Inject a bit error; the checker must go sticky.
        apb_write(16'h0020, 32'h0000_001B); // enable|start|inject|loopback
        repeat (2) @(posedge clk);
        apb_write(16'h0020, 32'h0000_0013); // remove inject
        repeat (4) @(posedge clk);
        bist_snapshot(32'h0000_0013, ecnt, bcnt, bstat, c1);
        if (!bstat[0]) begin
            $display("ERROR: BIST error flag not set after injection");
            errors++;
        end
        if (ecnt < 32'd1) begin
            $display("ERROR: BIST error_count %0d expected >= 1 after injection",
                     ecnt);
            errors++;
        end

        // e) clear while paused must zero counters and the sticky error.
        apb_write(16'h0020, 32'h0000_0014); // clear|loopback, start=0
        bist_snapshot(32'h0000_0014, ecnt, bcnt, bstat, c1);
        if (bstat[0]) begin
            $display("ERROR: BIST error flag still set after clear");
            errors++;
        end
        if (ecnt !== 32'd0) begin
            $display("ERROR: BIST error_count %0d not cleared", ecnt);
            errors++;
        end
        if (bcnt !== 32'd0) begin
            $display("ERROR: BIST bit_count %0d not cleared", bcnt);
            errors++;
        end

        // f) Normal-mode internal loopback: BIST off, RX fed from tx_serial_out.
        apb_write(16'h0020, 32'h0000_0010); // loopback only
        apb_write(16'h0000, 32'h0000_0003); // tx_enable | rx_enable
        send_word(8'hBC, 1'b0);
        for (int w = 0; w < WORDS; w++)
            send_word(payload[w*DW +: DW], (w == WORDS-1));
        for (int w = 0; w < WORDS; w++) begin
            read_beat(data, last);
            if (data !== payload[w*DW +: DW]) begin
                $display("ERROR: loopback RX word %0d got %h expected %h",
                         w, data, payload[w*DW +: DW]);
                errors++;
            end
            if (last !== (w == WORDS-1)) begin
                $display("ERROR: loopback RX tlast wrong at word %0d", w);
                errors++;
            end
        end

        // ------------------------------------------------------------------
        // Word/datapath BIST: PRBS payload through serializer + RX core.
        // ------------------------------------------------------------------
        apb_write(16'h0000, 32'h0000_0000);
        apb_write(16'h0020, 32'h0000_0047); // enable|start|clear|word_mode
        apb_write(16'h0020, 32'h0000_0043); // enable|start|word_mode
        repeat (1200) @(posedge clk);

        bist_snapshot(32'h0000_0043, ecnt, bc1, bstat, stamp);
        if (!bstat[2] || !bstat[8] || bstat[9]) begin
            $display("ERROR: word BIST status=%h expected valid+word_mode, no error",
                     bstat);
            errors++;
        end
        apb_read(16'h0034, ecnt); // word bit errors
        apb_read(16'h0038, bc1);  // word frames
        if (ecnt != 32'd0) begin
            $display("ERROR: word BIST clean error_count=%0d", ecnt);
            errors++;
        end
        if (bc1 < 32'd8) begin
            $display("ERROR: word BIST only %0d frames", bc1);
            errors++;
        end

        // Corrupt the serial stream for long enough to hit payload bits.
        @(negedge clk); inject_bit = 1'b1;
        repeat (200) @(posedge clk);
        @(negedge clk); inject_bit = 1'b0;
        repeat (400) @(posedge clk);
        bist_snapshot(32'h0000_0043, ecnt, bc1, bstat, stamp);
        if (!bstat[9]) begin
            $display("ERROR: word BIST injection not observed status=%h", bstat);
            errors++;
        end
        apb_read(16'h0034, ecnt);
        if (ecnt == 32'd0) begin
            $display("ERROR: word BIST error_count still 0 after injection");
            errors++;
        end

        // Clear while paused.
        apb_write(16'h0020, 32'h0000_0044); // word_mode|clear, start=0
        bist_snapshot(32'h0000_0044, ecnt, bc1, bstat, stamp);
        apb_read(16'h0034, ecnt);
        apb_read(16'h0038, bc1);
        if (bstat[9] || ecnt != 32'd0 || bc1 != 32'd0) begin
            $display("ERROR: word BIST clear failed status=%h err=%0d frames=%0d",
                     bstat, ecnt, bc1);
            errors++;
        end

        if (errors != 0) begin
            $display("FAIL: tb_serdes_soc_wrapper errors=%0d", errors);
            $fatal(1);
        end
        $display("PASS: tb_serdes_soc_wrapper");
        $finish;
    end

endmodule
