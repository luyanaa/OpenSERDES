`timescale 1ns/1ps
// 64b/66b data-block round-trip + stateful scrambler check.
//
// The encoder and decoder run off the same clock here (loopback).  `advance`
// is pulsed once per block so the scrambler LFSR carries state across blocks
// (real 64b/66b behaviour); the decoder self-synchronises after 58 bits.
module tb_64b66b;
    logic clk = 0, rst_n = 0;
    logic [63:0] data_in, data_out;
    logic [65:0] block, bad_block;
    logic sync_ok, ctrl_block, enc_ok, bad_sync_ok, bad_ctrl;
    logic ctrl = 0;
    logic advance = 0;
    int errors = 0;
    always #5 clk = ~clk;

    pcs_64b66b_encoder u_enc(.clk(clk), .rst_n(rst_n), .advance(advance),
                             .data_in(data_in), .ctrl(ctrl),
                             .block_out(block), .sync_ok(enc_ok));
    pcs_64b66b_decoder u_dec(.clk(clk), .rst_n(rst_n), .advance(advance),
                             .block_in(block), .data_out(data_out),
                             .sync_ok(sync_ok), .ctrl_block(ctrl_block));
    pcs_64b66b_decoder u_bad_dec(.clk(clk), .rst_n(rst_n), .advance(1'b0),
                                 .block_in(bad_block), .data_out(),
                                 .sync_ok(bad_sync_ok), .ctrl_block(bad_ctrl));

    // Pulse advance for one clock to commit one block.
    task step;
        begin @(negedge clk); advance = 1; @(negedge clk); advance = 0; end
    endtask

    initial begin
        logic [65:0] b1, b2;
        repeat (3) @(posedge clk); rst_n = 1; repeat (2) @(posedge clk);

        // Stateful scrambler: identical payload back-to-back must NOT give the
        // same scrambled block (state persists across blocks).
        data_in = 64'h0123456789ABCDEF; #1;
        step(); b1 = block;
        step(); b2 = block;
        if (b1 === b2) begin
            $display("FAIL scrambler state lost: identical blocks b1=b2=%018h", b1);
            errors++;
        end

        // Round-trip a stream of varied payloads.
        rst_n = 0; repeat (2) @(posedge clk); rst_n = 1; repeat (2) @(posedge clk);
        for (int i = 0; i < 256; i++) begin
            data_in = 64'h0123456789ABCDEF ^ (64'h9E3779B97F4A7C15 * i);
            #1; step(); #1;
            if (!enc_ok || !sync_ok || ctrl_block || data_out !== data_in) begin
                $display("FAIL i=%0d in=%016h out=%016h block=%018h sync=%0d",
                         i, data_in, data_out, block, sync_ok);
                errors++;
                if (errors > 3) $finish;
            end
        end

        // all-zero / all-one data blocks still round-trip
        for (int p = 0; p < 2; p++) begin
            data_in = p ? '1 : '0; #1; step(); #1;
            if (!sync_ok || data_out !== data_in) begin
                $display("FAIL pattern p=%0d out=%016h", p, data_out); errors++;
            end
        end

        // control block header
        ctrl = 1'b1; data_in = 64'hDEAD_BEEF_0123_4567; #1; step(); #1;
        if (!enc_ok || !sync_ok || !ctrl_block || data_out !== data_in) begin
            $display("FAIL control block sync=%0d ctrl=%0d out=%016h", sync_ok, ctrl_block, data_out);
            errors++;
        end
        ctrl = 1'b0;

        // invalid sync header must be rejected
        data_in = 64'h0; #1; step(); #1;
        bad_block = block; bad_block[65:64] = 2'b00; #1;
        if (bad_sync_ok) begin $display("FAIL invalid sync accepted"); errors++; end

        if (errors) $display("FAIL 64b66b errors=%0d", errors);
        else $display("PASS 64b66b stateful scrambler + data/control + invalid sync");
        $finish;
    end
    initial begin #200000; $display("FAIL timeout"); $finish; end
endmodule
