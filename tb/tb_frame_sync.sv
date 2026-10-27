`timescale 1ns/1ps

// Comma/frame-alignment unit test.
//
// Exercises the align/flush capture gating directly:
//   - comma 0xBC is discarded and the next bit becomes word0 bit0;
//   - a full 4-byte payload is captured;
//   - a second frame re-aligns after a gap;
//   - a mid-frame flush discards the partial frame and allows re-acquisition.
module tb_frame_sync;

    localparam int DW    = 8;
    localparam int WORDS = 4;
    localparam int RATIO = DW * WORDS;

    logic clk;
    logic rst_n;
    logic flush;
    logic sin;
    logic sval;
    logic align;
    logic locked;
    logic par_valid;
    logic busy;
    logic [WORDS-1:0][DW-1:0] par_data;

    int errors;
    int align_count;

    always #5 clk = ~clk;

    comma_align #(
        .PATTERN_LEN(8),
        .PATTERN(8'hBC),
        .LSB_FIRST(1'b1)
    ) u_align (
        .clk(clk),
        .rst_n(rst_n),
        .enable(1'b1),
        .flush(flush),
        .rearm(par_valid),
        .serial_in(sin),
        .serial_valid(sval),
        .align(align),
        .locked(locked)
    );

    deserializer #(
        .DATA_WIDTH(DW),
        .WORDS(WORDS),
        .LSB_FIRST(1'b1)
    ) u_des (
        .clk(clk),
        .rst_n(rst_n),
        .enable(1'b1),
        .align(align),
        .flush(flush),
        .serial_in(sin),
        .serial_valid(sval),
        .par_data(par_data),
        .par_valid(par_valid),
        .busy(busy)
    );

    always @(posedge clk) begin
        if (rst_n && align)
            align_count <= align_count + 1;
    end

    task automatic send_bit(input logic b);
        begin
            @(negedge clk);
            sin  = b;
            sval = 1'b1;
            @(posedge clk);
            #1;
            sval = 1'b0;
        end
    endtask

    task automatic send_byte(input logic [7:0] b);
        begin
            for (int i = 0; i < 8; i++)
                send_bit(b[i]);
        end
    endtask

    task automatic send_frame(input logic [RATIO-1:0] payload);
        int t;
        begin
            send_byte(8'hBC);
            for (int w = 0; w < WORDS; w++)
                send_byte(payload[w*DW +: DW]);

            t = 0;
            while (!par_valid && t < 16) begin
                @(posedge clk);
                #1;
                t++;
            end
            if (!par_valid) begin
                $display("ERROR: no par_valid after frame");
                errors++;
            end else if ({par_data[3], par_data[2], par_data[1], par_data[0]}
                         !== payload) begin
                $display("ERROR: frame %h, got %h", payload,
                         {par_data[3], par_data[2], par_data[1], par_data[0]});
                errors++;
            end
            @(posedge clk);
            #1;
        end
    endtask

    initial begin
        clk         = 1'b0;
        rst_n       = 1'b0;
        flush       = 1'b0;
        sin         = 1'b0;
        sval        = 1'b0;
        errors      = 0;
        align_count = 0;

        repeat (3) @(posedge clk);
        rst_n = 1'b1;
        repeat (2) @(posedge clk);

        send_frame(32'h0123_4567);
        send_frame(32'h89AB_CDEF);
        if (!locked) begin
            $display("ERROR: comma did not lock");
            errors++;
        end

        // Gap: no valid bits between frames.
        repeat (5) @(posedge clk);
        send_frame(32'hA5A5_5A5A);

        // Mid-frame flush: send a partial frame, then flush and re-acquire.
        send_byte(8'hBC);
        for (int i = 0; i < 10; i++)
            send_bit(1'b1);
        @(negedge clk);
        flush = 1'b1;
        @(posedge clk);
        #1;
        flush = 1'b0;
        @(posedge clk);
        #1;
        if (locked) begin
            $display("ERROR: comma locked survived flush");
            errors++;
        end
        send_frame(32'hCAFE_F00D);

        if (align_count < 4) begin
            $display("ERROR: too few align pulses: %0d", align_count);
            errors++;
        end
        if (errors != 0) begin
            $display("FAIL: tb_frame_sync errors=%0d align_count=%0d",
                     errors, align_count);
            $fatal(1);
        end
        $display("PASS: tb_frame_sync align_count=%0d", align_count);
        $finish;
    end

endmodule
