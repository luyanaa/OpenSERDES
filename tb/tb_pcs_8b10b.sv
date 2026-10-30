`timescale 1ns/1ps
module tb_pcs_8b10b;
    logic clk = 0, rst_n = 0, enable = 1, valid = 0;
    logic [7:0] tx_data, rx_data;
    logic ctrl = 0, tx_valid, tx_err, rx_valid, rx_ctrl, rx_code_err, rx_disp_err;
    logic [9:0] code;
    int errors = 0;
    always #5 clk = ~clk;

    pcs_8b10b_tx u_tx(.clk(clk), .rst_n(rst_n), .enable(enable), .valid(valid),
                       .data(tx_data), .ctrl(ctrl), .code(code),
                       .code_valid(tx_valid), .code_error(tx_err));
    pcs_8b10b_rx u_rx(.clk(clk), .rst_n(rst_n), .enable(enable), .valid(tx_valid),
                       .code(code), .data(rx_data), .ctrl(rx_ctrl),
                       .data_valid(rx_valid), .code_error(rx_code_err),
                       .disp_error(rx_disp_err));

    task automatic send(input logic [7:0] v, input logic c);
        begin
            @(negedge clk); tx_data = v; ctrl = c; valid = 1;
            @(posedge clk); @(negedge clk); valid = 0;
            @(posedge clk); @(negedge clk); #1;
        end
    endtask

    initial begin
        repeat (3) @(posedge clk); rst_n = 1; repeat (2) @(posedge clk);
        for (int b = 0; b < 256; b++) begin
            send(8'(b), 0);
            if (!rx_valid || rx_data !== 8'(b) || rx_ctrl || rx_code_err || rx_disp_err) begin
                $display("FAIL byte=%02h got=%02h valid=%0d ctrl=%0d code=%0d disp=%0d",
                         b, rx_data, rx_valid, rx_ctrl, rx_code_err, rx_disp_err);
                errors++;
                if (errors > 3) $finish;
            end
        end
        send(8'hBC, 1);
        if (!rx_valid || rx_data !== 8'hBC || !rx_ctrl || rx_code_err || rx_disp_err) begin
            $display("FAIL K28.5 got=%02h valid=%0d ctrl=%0d code=%0d disp=%0d",
                     rx_data, rx_valid, rx_ctrl, rx_code_err, rx_disp_err);
            errors++;
        end
        if (errors) $display("FAIL pcs_8b10b errors=%0d", errors);
        else $display("PASS pcs_8b10b 256 data bytes + K28.5");
        $finish;
    end
endmodule
