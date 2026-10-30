`timescale 1ns/1ps
module tb_64b66b;
    logic [63:0] data_in, data_out;
    logic [65:0] block, bad_block;
    logic sync_ok, ctrl_block, enc_ok, bad_sync_ok, bad_ctrl;
    logic ctrl = 0;
    int errors = 0;
    pcs_64b66b_encoder u_enc(.data_in(data_in), .ctrl(ctrl), .block_out(block), .sync_ok(enc_ok));
    pcs_64b66b_decoder u_dec(.block_in(block), .data_out(data_out),
                             .sync_ok(sync_ok), .ctrl_block(ctrl_block));
    pcs_64b66b_decoder u_bad_dec(.block_in(bad_block), .data_out(),
                                 .sync_ok(bad_sync_ok), .ctrl_block(bad_ctrl));
    initial begin
        for (int i = 0; i < 256; i++) begin
            data_in = 64'h0123456789ABCDEF ^ (64'h9E3779B97F4A7C15 * i);
            #1;
            if (!enc_ok || !sync_ok || ctrl_block || data_out !== data_in) begin
                $display("FAIL i=%0d in=%016h out=%016h block=%018h sync=%0d",
                         i, data_in, data_out, block, sync_ok);
                errors++;
                if (errors > 3) $finish;
            end
        end
        // all zero / all ones are valid data blocks too
        for (int p = 0; p < 2; p++) begin
            data_in = p ? '1 : '0;
            #1;
            if (!sync_ok || data_out !== data_in) begin
                $display("FAIL pattern p=%0d out=%016h", p, data_out); errors++;
            end
        end
        // control block header: valid sync, ctrl_block asserted, payload round-trips
        ctrl = 1'b1; data_in = 64'hDEAD_BEEF_0123_4567; #1;
        if (!enc_ok || !sync_ok || !ctrl_block || data_out !== data_in) begin
            $display("FAIL control block sync=%0d ctrl=%0d out=%016h", sync_ok, ctrl_block, data_out);
            errors++;
        end
        ctrl = 1'b0;
        // invalid sync header must be flagged
        data_in = 64'h0; #1;
        bad_block = block; bad_block[65:64] = 2'b00; #1;
        if (bad_sync_ok) begin $display("FAIL invalid sync accepted"); errors++; end
        if (errors) $display("FAIL 64b66b errors=%0d", errors);
        else $display("PASS 64b66b data blocks + invalid sync rejection");
        $finish;
    end
endmodule
