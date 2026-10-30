`timescale 1ns/1ps

// End-to-end 8b/10b comma realignment: a serial stream carrying K28.5 commas
// and data symbols is fed to the comma detector (pcs_8b10b_comma) driving a
// 10-bit deserializer (deserializer DATA_WIDTH=10, WORDS=1) then pcs_8b10b_rx.
// The stream starts deliberately misaligned; the detector must find the comma
// boundary, realign, and the decoder must recover the bytes.
module tb_pcs_8b10b_realign;
    logic clk = 0, rst_n = 0;
    int errors = 0;
    always #5 clk = ~clk;

    // TX side: encode bytes (and a K28.5 comma) into 10-bit symbols.
    logic t_en = 1, t_valid = 0;
    logic [7:0] t_data = 0; logic t_ctrl = 0;
    logic [9:0] t_code; logic t_cvalid, t_cerr;
    pcs_8b10b_tx u_tx (.clk(clk), .rst_n(rst_n), .enable(t_en), .valid(t_valid),
                       .data(t_data), .ctrl(t_ctrl), .code(t_code),
                       .code_valid(t_cvalid), .code_error(t_cerr));

    // Serial channel: we push encoded 10-bit symbols, LSB-first, through a
    // manual bit shifter into the comma detector + deserializer.
    logic c_sin = 0, c_sval = 0;
    logic c_align, c_locked, c_seen;
    pcs_8b10b_comma #(.LOCK_COMMAS(2)) u_comma (
        .clk(clk), .rst_n(rst_n), .enable(1'b1), .flush(1'b0),
        .serial_in(c_sin), .serial_valid(c_sval), .slip_req(1'b0),
        .align(c_align), .locked(c_locked), .comma_seen(c_seen)
    );

    logic des_en = 1;
    logic [0:0][9:0] par;
    logic par_valid, busy;
    deserializer #(.DATA_WIDTH(10), .WORDS(1), .LSB_FIRST(1)) u_des (
        .clk(clk), .rst_n(rst_n), .enable(des_en), .align(c_align),
        .flush(1'b0), .serial_in(c_sin), .serial_valid(c_sval),
        .par_data(par), .par_valid(par_valid), .busy(busy)
    );

    logic [7:0] rx_data; logic rx_ctrl, rx_dvalid, rx_cerr, rx_derr;
    pcs_8b10b_rx u_rx (.clk(clk), .rst_n(rst_n), .enable(1'b1),
                       .valid(par_valid), .code(par[0]), .data(rx_data),
                       .ctrl(rx_ctrl), .data_valid(rx_dvalid),
                       .code_error(rx_cerr), .disp_error(rx_derr));

    // ------------------------------------------------------------------
    // Bit pusher: serialize a 10-bit symbol LSB-first onto the wire.
    // ------------------------------------------------------------------
    task automatic push_symbol(input logic [9:0] sym);
        begin
            for (int b = 0; b < 10; b++) begin
                @(negedge clk); c_sin = sym[b]; c_sval = 1;
                @(negedge clk); c_sval = 0;
            end
        end
    endtask

    // Encode a byte via the TX (captures running disparity correctly).
    task automatic enc(input logic [7:0] d, input logic c, output logic [9:0] sym);
        begin
            @(negedge clk); t_data = d; t_ctrl = c; t_valid = 1;
            @(posedge clk); @(negedge clk); t_valid = 0;
            @(posedge clk); @(negedge clk); #1; sym = t_code;
        end
    endtask

    // Collect decoded bytes.
    logic [7:0] got [0:63];
    int got_n = 0;
    always_ff @(posedge clk) if (rx_dvalid) begin
        got[got_n] <= rx_data; got_n <= got_n + 1;
    end

    initial begin
        logic [9:0] sym;
        logic [7:0] payload [0:5];

        payload[0]=8'h11; payload[1]=8'h22; payload[2]=8'h33;
        payload[3]=8'h44; payload[4]=8'h55; payload[5]=8'h66;

        repeat (3) @(posedge clk); rst_n = 1; repeat (2) @(posedge clk);

        // Emit a misaligned preamble: a partial symbol then K28.5 commas.
        enc(8'hBC, 1'b1, sym);            // K28.5
        // push only 6 bits of an arbitrary symbol to misalign by ~4 bits
        enc(8'h00, 1'b0, sym);
        for (int b = 0; b < 6; b++) begin
            @(negedge clk); c_sin = sym[b]; c_sval = 1;
            @(negedge clk); c_sval = 0;
        end

        // Now send two K28.5 commas to establish lock.
        enc(8'hBC, 1'b1, sym); push_symbol(sym);
        enc(8'hBC, 1'b1, sym); push_symbol(sym);
        repeat (4) @(posedge clk);
        if (!c_locked) begin
            $display("FAIL comma did not lock"); errors++;
        end

        // Send payload symbols; after realignment they should decode cleanly.
        got_n = 0;
        for (int i = 0; i < 6; i++) begin
            enc(payload[i], 1'b0, sym); push_symbol(sym);
        end
        repeat (30) @(posedge clk);

        // Some early symbols may be misaligned garbage; the tail should decode
        // the last payload bytes correctly once locked.  Check we decoded the
        // payload sequence (allow leading garbage bytes before lock).
        begin
            int found = 0;
            for (int k = 0; k + 6 <= got_n; k++) begin
                if (got[k]==8'h11 && got[k+1]==8'h22 && got[k+2]==8'h33 &&
                    got[k+3]==8'h44 && got[k+4]==8'h55 && got[k+5]==8'h66)
                    found = 1;
            end
            if (!found && got_n < 6) begin
                $display("FAIL payload not decoded: got_n=%0d", got_n); errors++;
            end else if (!found) begin
                // print and fail
                $display("FAIL payload seq not found in %0d bytes", got_n);
                for (int k=0;k<got_n;k++) $display("  got[%0d]=%02h",k,got[k]);
                errors++;
            end
        end

        if (errors) $display("FAIL tb_pcs_8b10b_realign errors=%0d", errors);
        else $display("PASS tb_pcs_8b10b_realign: comma lock + realigned decode");
        $finish;
    end
    initial begin #100000; $display("FAIL timeout"); $finish; end
endmodule
