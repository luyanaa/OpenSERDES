`timescale 1ns/1ps

module tb_8b10b;
    logic clk = 0, rst_n = 0, en = 1;
    logic [7:0] enc_in, dec_out;
    logic enc_ctrl = 0, enc_err, enc_en = 1;
    logic [9:0] enc_out, dec_in;
    logic dec_ctrl, dec_code_err, dec_disp_err;
    int errors = 0;

    always #5 clk = ~clk;

    encoder_8b10b u_enc (
        .i_clk(clk), .i_reset_n(rst_n), .i_en(enc_en),
        .i_8b(enc_in), .i_ctrl(enc_ctrl),
        .o_10b(enc_out), .o_code_err(enc_err)
    );

    decoder_8b10b u_dec (
        .i_clk(clk), .i_reset_n(rst_n), .i_en(en),
        .i_10b(dec_in), .o_8b(dec_out), .o_ctrl(dec_ctrl),
        .o_code_err(dec_code_err), .o_disp_err(dec_disp_err)
    );

    task automatic send(input logic [7:0] v, input logic ctrl);
        begin
            @(negedge clk);
            enc_in = v; enc_ctrl = ctrl; enc_en = 1'b1;
            @(posedge clk);
            @(negedge clk);
            enc_en = 1'b0;
            dec_in = enc_out;       // stable encoded symbol
            @(posedge clk);         // decoder samples
            @(negedge clk);
            #1;
        end
    endtask

    initial begin
        repeat (3) @(posedge clk);
        rst_n = 1'b1;
        repeat (2) @(posedge clk);
        for (int b = 0; b < 256; b++) begin
            send(8'(b), 0);
            #1;
            if (dec_out !== 8'(b) || dec_ctrl !== 1'b0 || dec_code_err || dec_disp_err) begin
                $display("FAIL data byte=%02h dec=%02h ctrl=%0d code_err=%0d disp_err=%0d code=%b",
                         b, dec_out, dec_ctrl, dec_code_err, dec_disp_err, enc_out);
                errors++;
                if (errors > 5) $finish;
            end
        end
        send(8'hBC, 1); // K28.5
        #1;
        if (dec_out !== 8'hBC || dec_ctrl !== 1'b1 || dec_code_err || dec_disp_err) begin
            $display("FAIL K28.5 dec=%02h ctrl=%0d code_err=%0d disp_err=%0d code=%b",
                     dec_out, dec_ctrl, dec_code_err, dec_disp_err, enc_out);
            errors++;
        end
        if (errors) begin
            $display("FAIL 8b10b errors=%0d", errors);
        end else begin
            $display("PASS 8b10b 256 data bytes + K28.5");
        end
        $finish;
    end
endmodule
