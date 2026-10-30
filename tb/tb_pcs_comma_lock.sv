`timescale 1ns/1ps

// K28.5 comma detect + 64b/66b block-lock FSM.
module tb_pcs_comma_lock;
    logic clk = 0, rst_n = 0;
    int errors = 0;
    always #5 clk = ~clk;

    // ---------------------------------------------------------------------
    // 8b/10b comma detector
    // ---------------------------------------------------------------------
    logic c_en = 1, c_flush = 0, c_sin = 0, c_sval = 0, c_slip = 0;
    logic c_align, c_locked, c_seen;
    pcs_8b10b_comma #(.LOCK_COMMAS(2)) u_comma (
        .clk(clk), .rst_n(rst_n), .enable(c_en), .flush(c_flush),
        .serial_in(c_sin), .serial_valid(c_sval), .slip_req(c_slip),
        .align(c_align), .locked(c_locked), .comma_seen(c_seen)
    );

    // Feed a bit of a serial stream.
    task automatic cbit(input logic b);
        begin @(negedge clk); c_sin = b; c_sval = 1; @(negedge clk); c_sval = 0; end
    endtask

    // Send the K28.5 rd- symbol LSB-first (wire bits 0,0,1,1,1,1,1,0,1,0)
    // then a few filler bits.
    task automatic send_comma;
        begin
            cbit(0); cbit(0); cbit(1); cbit(1); cbit(1); cbit(1); cbit(1); // comma 0011111
            cbit(0); cbit(1); cbit(0);                                     // rest of symbol
        end
    endtask

    // ---------------------------------------------------------------------
    // 64b/66b block lock
    // ---------------------------------------------------------------------
    logic b_en = 1, b_valid = 0;
    logic [1:0] b_hdr = 2'b00;
    logic b_lock, b_pre, b_bad;
    pcs_64b66b_lock #(.GOOD_FOR_LOCK(4), .BAD_TO_UNLOCK(3), .WIN_BLOCKS(8)) u_lock (
        .clk(clk), .rst_n(rst_n), .enable(b_en),
        .blk_valid(b_valid), .sync_hdr(b_hdr),
        .block_lock(b_lock), .presync(b_pre), .bad_hdr(b_bad)
    );

    task automatic blk(input logic [1:0] h);
        begin @(negedge clk); b_hdr = h; b_valid = 1; @(negedge clk); b_valid = 0; end
    endtask

    initial begin
        repeat (3) @(posedge clk); rst_n = 1; repeat (2) @(posedge clk);

        // ---- comma: garbage bits then commas -> lock -----------------------
        for (int i = 0; i < 13; i++) cbit($urandom_range(0,1));
        send_comma();
        for (int i = 0; i < 10; i++) cbit($urandom_range(0,1)); // a symbol period
        send_comma();
        repeat (2) @(posedge clk);
        if (!c_locked) begin $display("FAIL comma not locked"); errors++; end

        // slip -> relock
        @(negedge clk); c_slip = 1; @(negedge clk); c_slip = 0;
        repeat (2) @(posedge clk);
        if (c_locked) begin $display("FAIL comma did not drop on slip"); errors++; end

        // ---- 64b66b block lock: bad -> hunt, good run -> lock --------------
        blk(2'b00); // illegal -> stays hunting
        blk(2'b11);
        if (b_lock || b_pre) begin $display("FAIL lock on illegal hdr"); errors++; end
        blk(2'b01); // good -> presync
        if (!b_pre) begin $display("FAIL no presync on good hdr"); errors++; end
        blk(2'b01); blk(2'b10); blk(2'b01); // reach GOOD_FOR_LOCK
        if (!b_lock) begin $display("FAIL no block_lock after good run"); errors++; end

        // a couple bad headers within window should not unlock (under thresh)
        blk(2'b00); blk(2'b01);
        if (!b_lock) begin $display("FAIL dropped lock too easily"); errors++; end

        // sustained bad headers -> unlock
        for (int i = 0; i < 8; i++) blk(2'b00);
        if (b_lock) begin $display("FAIL did not unlock on bad run"); errors++; end

        if (errors) $display("FAIL tb_pcs_comma_lock errors=%0d", errors);
        else $display("PASS tb_pcs_comma_lock: K28.5 lock/slip + 64b66b hunt/presync/lock/unlock");
        $finish;
    end
    initial begin #30000; $display("FAIL timeout"); $finish; end
endmodule
