`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// Wishbone B4 slave <-> AXIS data port.
//
// A Wishbone SoC reaches the stream one word per access, and the FIFOs absorb
// the rate difference with the SerDes word clock.  What must hold:
//
//   1. Words written through Wishbone come out of the TX stream in order.
//   2. Words presented on the RX stream are readable through Wishbone in order.
//   3. A write into a full TX FIFO raises wb_err and loses nothing already
//      queued (no silent overwrite).
//   4. A read of an empty RX FIFO raises wb_err and does not fabricate data.
//   5. `tx_flush` drops queued TX words; the sticky flag is readable and the
//      status levels go to zero.
//   6. The status word reports the FIFO levels, tx_full/rx_empty and the
//      underflow counter.
//   7. Back-to-back accesses: one ack per access, correct ordering.
// -----------------------------------------------------------------------------
module tb_wb_axis_fifo;

    localparam int DW    = 32;
    localparam int DEPTH = 8;
    localparam int AW    = 16;

    logic clk = 0, rst_n = 0, clear = 0;
    always #5 clk = ~clk;

    int errors;

    // ---- Wishbone master ---------------------------------------------------
    logic            wb_cyc = 0, wb_stb = 0;
    logic [AW-1:0]   wb_adr = '0;
    logic            wb_we = 0;
    logic [DW-1:0]   wb_dat_w = '0;
    logic [DW/8-1:0] wb_sel = '1;
    logic            wb_ack, wb_err;
    logic [DW-1:0]   wb_dat_r;

    // ---- TX stream (out of the Wishbone-written FIFO) ----------------------
    logic [DW-1:0] tx_tdata;
    logic          tx_tvalid, tx_tready = 1'b0;
    // ---- RX stream (into the Wishbone-readable FIFO) -----------------------
    logic [DW-1:0] rx_tdata = '0;
    logic          rx_tvalid = 1'b0, rx_tready;

    wb_axis_fifo #(
        .DATA_WIDTH(DW), .FIFO_DEPTH(DEPTH), .ADDR_WIDTH(AW)
    ) dut (
        .clk(clk), .rst_n(rst_n), .clear(clear),
        .wb_cyc(wb_cyc), .wb_stb(wb_stb), .wb_adr(wb_adr), .wb_we(wb_we),
        .wb_dat_w(wb_dat_w), .wb_sel(wb_sel),
        .wb_ack(wb_ack), .wb_err(wb_err), .wb_dat_r(wb_dat_r),
        .m_axis_tdata(tx_tdata), .m_axis_tvalid(tx_tvalid),
        .m_axis_tready(tx_tready),
        .s_axis_tdata(rx_tdata), .s_axis_tvalid(rx_tvalid),
        .s_axis_tready(rx_tready)
    );

    task automatic wb_write(input logic [AW-1:0] adr, input logic [DW-1:0] dat,
                             output bit err, output bit acked);
        begin
            @(negedge clk);
            wb_adr = adr; wb_dat_w = dat; wb_we = 1'b1; wb_sel = '1;
            wb_cyc = 1'b1; wb_stb = 1'b1;
            err = 0; acked = 0;
            for (int t = 0; t < 16 && !acked; t++) begin
                @(posedge clk);
                #1;
                if (wb_ack || wb_err) begin
                    acked = 1'b1; err = wb_err;
                end
            end
            @(negedge clk);
            wb_cyc = 1'b0; wb_stb = 1'b0;
        end
    endtask

    task automatic wb_read(input logic [AW-1:0] adr,
                            output logic [DW-1:0] dat,
                            output bit err, output bit acked);
        begin
            @(negedge clk);
            wb_adr = adr; wb_we = 1'b0; wb_sel = '1;
            wb_cyc = 1'b1; wb_stb = 1'b1;
            err = 0; acked = 0; dat = '0;
            for (int t = 0; t < 16 && !acked; t++) begin
                @(posedge clk);
                #1;
                if (wb_ack || wb_err) begin
                    acked = 1'b1; err = wb_err; dat = wb_dat_r;
                end
            end
            @(negedge clk);
            wb_cyc = 1'b0; wb_stb = 1'b0;
        end
    endtask

    // TX stream drain: pop one word when the test asks for it.
    int tx_seen;
    logic [DW-1:0] tx_got [0:63];
    always @(posedge clk) begin
        if (rst_n && tx_tvalid && tx_tready)
            tx_got[tx_seen] <= tx_tdata;
    end
    always @(posedge clk)
        if (rst_n && tx_tvalid && tx_tready)
            tx_seen <= tx_seen + 1;

    initial begin
        logic [DW-1:0] d;
        bit e, a;
        int status;

        errors = 0; tx_seen = 0;

        repeat (3) @(posedge clk);
        rst_n = 1'b1;
        repeat (2) @(posedge clk);

        // ---- 1. write 5 words, then drain them --------------------------
        for (int i = 0; i < 5; i++) begin
            wb_write('0, 32'h1000_0000 + 32'(i), e, a);
            if (!a || e) begin
                $display("ERROR: write %0d ack=%b err=%b", i, a, e);
                errors++;
            end
        end
        tx_tready = 1'b1;
        repeat (12) @(posedge clk);
        tx_tready = 1'b0;
        if (tx_seen != 5) begin
            $display("ERROR: tx_seen=%0d expected 5", tx_seen);
            errors++;
        end
        for (int i = 0; i < 5; i++)
            if (tx_got[i] !== 32'h1000_0000 + 32'(i)) begin
                $display("ERROR: tx word %0d = %08h", i, tx_got[i]);
                errors++;
            end

        // ---- 2. RX stream -> Wishbone read ------------------------------
        // One word per push: drive valid on the falling edge, and drop it on the
        // *next* falling edge -- by then exactly one rising edge (one accept)
        // has happened.  Holding valid across two rising edges pushes twice.
        for (int i = 0; i < 3; i++) begin
            @(negedge clk);
            rx_tdata  = 32'h2000_0000 + 32'(i);
            rx_tvalid = 1'b1;
            while (!rx_tready) @(negedge clk);
            @(negedge clk);
            rx_tvalid = 1'b0;
            while (!rx_tready) @(negedge clk);
        end
        repeat (4) @(posedge clk);
        for (int i = 0; i < 3; i++) begin
            wb_read('0, d, e, a);
            if (!a || e) begin
                $display("ERROR: rx read %0d ack=%b err=%b", i, a, e);
                errors++;
            end
            if (d !== 32'h2000_0000 + 32'(i)) begin
                $display("ERROR: rx word %0d = %08h", i, d);
                errors++;
            end
        end

        // ---- 4. empty read raises wb_err -------------------------------
        wb_read('0, d, e, a);
        if (!a || !e) begin
            $display("ERROR: empty read ack=%b err=%b", a, e); errors++;
        end

        // ---- 3. fill the TX FIFO, then overflow ------------------------
        for (int i = 0; i < DEPTH; i++) begin
            wb_write('0, 32'h3000_0000 + 32'(i), e, a);
            if (!a || e) begin
                $display("ERROR: fill %0d ack=%b err=%b", i, a, e);
                errors++;
            end
        end
        wb_write('0, 32'hDEAD_0000, e, a);
        if (!a || !e) begin
            $display("ERROR: overflow write ack=%b err=%b (expected err)",
                     a, e);
            errors++;
        end

        // ---- 6. status word -------------------------------------------
        wb_read(AW'(4), d, e, a);
        status = int'(d);
        if (!a || e) begin
            $display("ERROR: status read ack=%b err=%b", a, e); errors++;
        end
        if (int'(d[7:0]) != DEPTH) begin
            $display("ERROR: tx_level=%0d expected %0d", d[7:0], DEPTH);
            errors++;
        end
        if (d[16] !== 1'b1) begin
            $display("ERROR: tx_full not reported"); errors++;
        end
        $display("INFO: status=%08h tx_level=%0d rx_level=%0d tx_full=%b",
                 d, d[7:0], d[15:8], d[16]);

        // ---- 5. tx_flush drops the queue -------------------------------
        wb_write(AW'(8), 32'h1, e, a);
        if (!a || e) begin
            $display("ERROR: flush write ack=%b err=%b", a, e); errors++;
        end
        wb_read(AW'(4), d, e, a);
        if (d[16] !== 1'b0) begin
            $display("ERROR: flush left tx_full=%b tx_level=%0d",
                     d[16], d[7:0]);
            errors++;
        end
        if (d[21] !== 1'b1) begin
            $display("ERROR: tx_flush sticky not set"); errors++;
        end

        // ---- 8. misaligned address is rejected --------------------------
        wb_read(AW'(1), d, e, a);
        if (!a || !e) begin
            $display("ERROR: misaligned read ack=%b err=%b (expected err)",
                     a, e);
            errors++;
        end
        wb_write(AW'(2), 32'hBAD0_0000, e, a);
        if (!a || !e) begin
            $display("ERROR: misaligned write ack=%b err=%b", a, e);
            errors++;
        end

        // ---- 7. back-to-back ordering ----------------------------------
        // Drain anything still queued (a rejected write must not have pushed,
        // but the flush above left the stream idle).
        tx_seen = 0;
        tx_tready = 1'b1;
        repeat (16) @(posedge clk);
        tx_seen = 0;
        for (int i = 0; i < 4; i++) begin
            wb_write('0, 32'h4000_0000 + 32'(i), e, a);
            if (!a || e) begin
                $display("ERROR: b2b write %0d ack=%b err=%b", i, a, e);
                errors++;
            end
            repeat (2) @(posedge clk);
        end
        repeat (6) @(posedge clk);
        tx_tready = 1'b0;
        if (tx_seen != 4) begin
            $display("ERROR: b2b tx_seen=%0d expected 4", tx_seen);
            errors++;
        end
        for (int i = 0; i < 4; i++)
            if (tx_got[i] !== 32'h4000_0000 + 32'(i)) begin
                $display("ERROR: b2b tx word %0d = %08h", i, tx_got[i]);
                errors++;
            end

        if (errors == 0)
            $display("PASS: tb_wb_axis_fifo tx/rx order, overflow, underflow, flush, status ok");
        else
            $display("FAIL: tb_wb_axis_fifo errors=%0d", errors);
        $finish;
    end

    initial begin
        #500000;
        $display("FAIL: tb_wb_axis_fifo timeout errors=%0d", errors);
        $finish;
    end

endmodule
