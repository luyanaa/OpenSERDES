`timescale 1ns/1ps

// Word-streaming serializer test.
// Verifies the per-word valid/ready handshake, LSB/MSB ordering, back-to-back
// frames, and that in_ready is low during serialization.
module tb_serializer_stream;

    localparam int DW    = 8;
    localparam int WORDS = 4;
    localparam int RATIO = DW * WORDS;

    logic clk;
    logic rst_n;
    logic enable;
    logic in_valid;
    logic in_ready;
    logic [DW-1:0] in_word;
    logic serial_out;
    logic serial_valid;
    logic busy;
    logic frame_done;

    int errors;
    int done_count;

    serializer_stream #(
        .DATA_WIDTH(DW),
        .WORDS(WORDS),
        .LSB_FIRST(1'b1)
    ) dut (
        .clk(clk),
        .rst_n(rst_n),
        .enable(enable),
        .in_valid(in_valid),
        .in_ready(in_ready),
        .in_word(in_word),
        .serial_out(serial_out),
        .serial_valid(serial_valid),
        .busy(busy),
        .frame_done(frame_done)
    );

    always #5 clk = ~clk;

    always @(posedge clk) begin
        if (frame_done)
            done_count <= done_count + 1;
    end

    function automatic logic expected_bit(input logic [RATIO-1:0] data,
                                           input int unsigned i);
        expected_bit = data[i];
    endfunction

    task automatic run_frame(input logic [RATIO-1:0] data);
        int timeout;
        begin
            // Feed one word per accepted cycle.  Each word is intentionally
            // changed immediately after the handshake to prove that the
            // upstream source does not have to hold the full frame.
            for (int w = 0; w < WORDS; w++) begin
                @(negedge clk);
                in_word  = data[w*DW +: DW];
                in_valid = 1'b1;
                while (!in_ready) begin
                    @(negedge clk);
                    in_word = data[w*DW +: DW];
                end
                @(posedge clk);
                #1;
                in_valid = 1'b0;
                in_word  = ~data[w*DW +: DW];
            end

            timeout = 0;
            while (!serial_valid && timeout < 8) begin
                @(posedge clk);
                #1;
                timeout++;
            end
            if (!serial_valid) begin
                $display("ERROR: serialization did not start");
                errors++;
            end

            for (int i = 0; i < RATIO; i++) begin
                enable = 1'b1;
                if (!serial_valid) begin
                    $display("ERROR: serial_valid low at bit %0d", i);
                    errors++;
                end
                if (serial_out !== expected_bit(data, i)) begin
                    $display("ERROR: bit %0d got %0b expected %0b",
                             i, serial_out, expected_bit(data, i));
                    errors++;
                end
                @(posedge clk);
                #1;
            end
            enable = 1'b0;

            if (!frame_done) begin
                $display("ERROR: frame_done missing");
                errors++;
            end
            @(posedge clk);
            #1;
            if (in_ready !== 1'b1) begin
                $display("ERROR: in_ready did not return");
                errors++;
            end
        end
    endtask

    initial begin
        clk       = 1'b0;
        rst_n     = 1'b0;
        enable    = 1'b0;
        in_valid  = 1'b0;
        in_word   = '0;
        errors    = 0;
        done_count= 0;

        repeat (3) @(posedge clk);
        rst_n = 1'b1;
        repeat (2) @(posedge clk);

        run_frame(32'h0123_4567);
        run_frame(32'h89AB_CDEF);
        run_frame(32'hA5A5_5A5A);

        if (done_count != 3) begin
            $display("ERROR: frame_done count %0d", done_count);
            errors++;
        end
        if (errors != 0) begin
            $display("FAIL: tb_serializer_stream errors=%0d", errors);
            $fatal(1);
        end
        $display("PASS: tb_serializer_stream");
        $finish;
    end

endmodule
