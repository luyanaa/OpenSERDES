`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// 64b/66b RX gearbox + control decode.
//
// Wire order is the IEEE one: the 2-bit sync header is transmitted first, so
// the TB drives block[65] first down to block[0].  Coverage:
//   1. pcs_64b66b_ctrl_decode unit checks (data / idle / start / terminate /
//      mixed-data / illegal header / unknown block type).
//   2. Sliding-window lock from an arbitrary bit offset: 11 non-header bits
//      are prepended, then data blocks whose payload is a unique counter.
//      After lock, emitted payloads must map to strictly increasing sent
//      block indices (no duplication, no reordering).
//   3. A control block in the stream is emitted and classified ALL_CONTROL.
//   4. A dropped bit makes lock fall and re-acquire; post-relock blocks are
//      again valid data blocks that map forward in the sent sequence.
//   5. 200 idle cycles (no serial_valid) drop the lock.
// -----------------------------------------------------------------------------
module tb_64b66b_rx;

    localparam int MAXS = 64;

    logic clk, rst_n, enable, slip_req;
    logic serial_in, serial_valid;
    logic [65:0] block_out;
    logic blk_valid, blk_locked;
    logic [63:0] out_data;
    logic out_valid, out_ready, overflow;

    logic [65:0] cd_block, cd_in;
    logic        cd_force;
    logic [2:0]  kind;
    logic [7:0]  byte_ctrl, block_type;
    logic [63:0] ctrl_data;
    logic [6:0][6:0] ctrl_field;
    logic [6:0]  field_valid;
    logic        type_valid;

    int errors;
    int emitted;
    int data_emitted;
    int sent_n;
    int prev_idx;
    logic synced;
    logic lock_d;
    logic [63:0] sent_pay [0:MAXS-1];

    always #5 clk = ~clk;

    pcs_64b66b_rx_gearbox #(
        .LOCK_BLOCKS(4), .BAD_BLOCKS(4), .IDLE_TIMEOUT(128)
    ) dut (
        .clk(clk), .rst_n(rst_n), .enable(enable), .slip_req(slip_req),
        .serial_in(serial_in), .serial_valid(serial_valid),
        .block_out(block_out), .blk_valid(blk_valid), .blk_locked(blk_locked),
        .out_data(out_data), .out_valid(out_valid), .out_ready(out_ready),
        .overflow(overflow)
    );

    assign cd_block = cd_force ? cd_in : block_out;

    pcs_64b66b_ctrl_decode u_cd (
        .block_in(cd_block), .kind(kind), .byte_ctrl(byte_ctrl),
        .data(ctrl_data), .block_type(block_type),
        .ctrl_field(ctrl_field), .field_valid(field_valid),
        .type_valid(type_valid)
    );

    assign out_ready = 1'b1;

    task automatic send_bit(input logic b);
        begin
            @(negedge clk);
            serial_in    = b;
            serial_valid = 1'b1;
        end
    endtask

    // Return the line to idle without losing the last driven bit: the gearbox
    // samples it on the following posedge.
    task automatic stop_line;
        begin
            @(posedge clk);
            #1;
            serial_valid = 1'b0;
        end
    endtask

    // IEEE wire order: header (block[65:64]) first, then block[63]..block[0].
    task automatic send_block(input logic [65:0] blk);
        begin
            for (int i = 65; i >= 0; i--)
                send_bit(blk[i]);
            sent_pay[sent_n] = blk[63:0];
            sent_n++;
        end
    endtask

    // Same, but the last payload bit (block[0]) is not transmitted: a one-bit
    // slip that destroys the current block boundary.
    task automatic send_block_slip(input logic [65:0] blk);
        begin
            for (int i = 65; i >= 1; i--)
                send_bit(blk[i]);
            sent_pay[sent_n] = blk[63:0];
            sent_n++;
        end
    endtask

    task automatic check_ctrl(input logic [65:0] blk,
                              input logic [2:0] exp_kind,
                              input logic exp_valid);
        begin
            @(negedge clk);
            cd_force = 1'b1;
            cd_in    = blk;
            #1;
            if (kind !== exp_kind) begin
                $display("ERROR: ctrl kind %0d expected %0d", kind, exp_kind);
                errors++;
            end
            if (type_valid !== exp_valid) begin
                $display("ERROR: ctrl type_valid %b expected %b", type_valid, exp_valid);
                errors++;
            end
            cd_force = 1'b0;
        end
    endtask

    // Scoreboard: every emitted data block must be a block we sent, and the
    // sent index must advance (strictly) so blocks are neither duplicated nor
    // reordered.
    always @(posedge clk) begin
        if (rst_n && blk_valid) begin
            if (block_out[65:64] == 2'b01) begin
                int idx;
                idx = -1;
                for (int j = 0; j < sent_n; j++)
                    if (sent_pay[j] == block_out[63:0])
                        idx = j;
                if (idx >= 0) begin
                    if (synced && idx <= prev_idx) begin
                        $display("ERROR: payload index %0d after %0d", idx, prev_idx);
                        errors++;
                    end
                    prev_idx = idx;
                    synced   = 1'b1;
                end else if (synced) begin
                    $display("ERROR: emitted unknown payload %h", block_out[63:0]);
                    errors++;
                end
                data_emitted++;
            end
            emitted++;
            if (out_valid && (out_data !== block_out[63:0])) begin
                $display("ERROR: gearbox out_data %h != payload %h",
                         out_data, block_out[63:0]);
                errors++;
            end
        end
        if (rst_n && !blk_locked && lock_d) begin
            // The recovery window may contain fragments of the damaged block.
            synced   = 1'b0;
            prev_idx = -1;
        end
        lock_d <= blk_locked;
        if (rst_n && out_valid && !blk_valid) begin
            $display("ERROR: out_valid without blk_valid");
            errors++;
        end
    end

    initial begin
        logic [65:0] b;
        clk          = 1'b0;
        rst_n        = 1'b0;
        enable       = 1'b0;
        slip_req     = 1'b0;
        serial_in    = 1'b0;
        serial_valid = 1'b0;
        cd_force     = 1'b0;
        cd_in        = '0;
        errors       = 0;
        emitted      = 0;
        data_emitted = 0;
        sent_n       = 0;
        prev_idx     = -1;
        synced       = 1'b0;
        lock_d       = 1'b0;

        repeat (4) @(posedge clk);
        rst_n  = 1'b1;
        enable = 1'b1;
        repeat (4) @(posedge clk);

        // ---- 1. control decode unit checks -------------------------------
        check_ctrl({2'b01, 64'h0123_4567_89AB_CDEF}, 3'd0, 1'b1);
        check_ctrl({2'b10, 56'h0, 8'h00}, 3'd1, 1'b1);
        check_ctrl({2'b10, 56'h0, 8'h78}, 3'd2, 1'b1);
        check_ctrl({2'b10, 56'h0, 8'h99}, 3'd2, 1'b1);
        check_ctrl({2'b10, 56'h0, 8'h87}, 3'd3, 1'b1);
        check_ctrl({2'b10, 56'h0, 8'hFF}, 3'd4, 1'b1);
        check_ctrl({2'b10, 56'h0, 8'h55}, 3'd5, 1'b0);
        check_ctrl({2'b00, 64'h0}, 3'd5, 1'b0);
        check_ctrl({2'b11, 64'h0}, 3'd5, 1'b0);

        // ---- 2. sliding-window lock from an arbitrary offset -------------
        for (int i = 0; i < 11; i++)
            send_bit(1'b1);

        for (int i = 0; i < 6; i++) begin
            b = {2'b01, 40'h0, 24'(64'h1000 + i)};
            send_block(b);
        end
        stop_line();
        for (int i = 0; i < 40 && data_emitted < 2; i++)
            @(posedge clk);
        if (!blk_locked) begin
            $display("ERROR: gearbox did not lock after 6 data blocks");
            errors++;
        end
        if (data_emitted < 2) begin
            $display("ERROR: only %0d data blocks emitted", data_emitted);
            errors++;
        end

        // ---- 3. control block in the stream ------------------------------
        send_block({2'b10, 56'h0, 8'h00});
        stop_line();
        for (int i = 0; i < 20 && kind != 3'd1; i++)
            @(posedge clk);
        if (block_out[65:64] !== 2'b10 || block_type !== 8'h00 ||
            kind !== 3'd1 || type_valid !== 1'b1) begin
            $display("ERROR: streamed idle block hdr=%b type=%02h kind=%0d",
                     block_out[65:64], block_type, kind);
            errors++;
        end

        // ---- 4. bit slip: lock falls, re-acquires ------------------------
        send_block_slip({2'b01, 40'h0, 24'(64'h2000)});
        for (int i = 0; i < 10; i++) begin
            b = {2'b01, 40'h0, 24'(64'h2100 + i)};
            send_block(b);
        end
        stop_line();
        for (int i = 0; i < 4000 && !blk_locked; i++)
            @(posedge clk);
        if (!blk_locked) begin
            $display("ERROR: gearbox did not re-lock after the slip");
            errors++;
        end
        // Let the post-relock stream run so the scoreboard re-syncs.
        for (int i = 0; i < 10; i++)
            @(posedge clk);

        // ---- 5. idle timeout drops lock ---------------------------------
        serial_valid = 1'b0;
        for (int i = 0; i < 200; i++)
            @(posedge clk);
        if (blk_locked) begin
            $display("ERROR: lock survived 200 idle cycles");
            errors++;
        end
        if (overflow !== 1'b0) begin
            $display("ERROR: overflow asserted with out_ready tied high");
            errors++;
        end

        if (errors == 0)
            $display("PASS: tb_64b66b_rx blocks=%0d data_blocks=%0d errors=0",
                     emitted, data_emitted);
        else
            $display("FAIL: tb_64b66b_rx errors=%0d", errors);
        $finish;
    end

    initial begin
        #2000000;
        $display("FAIL: tb_64b66b_rx timeout errors=%0d", errors);
        $finish;
    end

endmodule
