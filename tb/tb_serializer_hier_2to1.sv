`timescale 1ns/1ps

// Bit-exact comparison of serializer_stream and serializer_hier_2to1.
module tb_serializer_hier_2to1 #(
    parameter int DATA_WIDTH = 32,
    parameter int WORDS      = 8,
    parameter bit LSB_FIRST  = 1'b1
);

    localparam int FRAMES = 200;

    logic clk;
    logic rst_n;
    logic enable;
    logic in_valid;
    logic in_ready_ref;
    logic in_ready_hier;
    logic [DATA_WIDTH-1:0] in_word;
    logic serial_ref;
    logic serial_hier;
    logic valid_ref;
    logic valid_hier;
    logic busy_ref;
    logic busy_hier;
    logic done_ref;
    logic done_hier;

    int errors;
    int frames_ref;
    int frames_hier;
    logic started;

    always #5 clk = ~clk;

    serializer_stream #(
        .DATA_WIDTH(DATA_WIDTH), .WORDS(WORDS), .LSB_FIRST(LSB_FIRST)
    ) u_ref (
        .clk(clk), .rst_n(rst_n), .enable(enable),
        .in_valid(in_valid), .in_ready(in_ready_ref), .in_word(in_word),
        .serial_out(serial_ref), .serial_valid(valid_ref),
        .busy(busy_ref), .frame_done(done_ref)
    );

    serializer_hier_2to1 #(
        .DATA_WIDTH(DATA_WIDTH), .WORDS(WORDS), .LSB_FIRST(LSB_FIRST)
    ) u_hier (
        .clk(clk), .rst_n(rst_n), .enable(enable),
        .in_valid(in_valid), .in_ready(in_ready_hier), .in_word(in_word),
        .serial_out(serial_hier), .serial_valid(valid_hier),
        .busy(busy_hier), .frame_done(done_hier)
    );

    always @(posedge clk) begin
        if (done_ref)
            frames_ref <= frames_ref + 1;
        if (done_hier)
            frames_hier <= frames_hier + 1;
    end

    always @(posedge clk) begin
        if (rst_n && started) begin
            #1;
            if (in_ready_ref !== in_ready_hier) begin
                $display("ERROR: in_ready mismatch ref=%b hier=%b", in_ready_ref, in_ready_hier);
                errors++;
            end
            if (serial_ref !== serial_hier) begin
                $display("ERROR: serial mismatch ref=%b hier=%b", serial_ref, serial_hier);
                errors++;
            end
            if (valid_ref !== valid_hier || busy_ref !== busy_hier) begin
                $display("ERROR: valid/busy mismatch ref=%b/%b hier=%b/%b",
                         valid_ref, busy_ref, valid_hier, busy_hier);
                errors++;
            end
            if (done_ref !== done_hier) begin
                $display("ERROR: frame_done mismatch ref=%b hier=%b", done_ref, done_hier);
                errors++;
            end
        end
    end

    task automatic send_word(input logic [DATA_WIDTH-1:0] w);
        begin
            @(negedge clk);
            in_word  = w;
            in_valid = 1'b1;
            while (!in_ready_ref) @(negedge clk);
            @(posedge clk);
            #1;
            in_valid = 1'b0;
        end
    endtask

    initial begin
        logic [DATA_WIDTH-1:0] frame_mem [0:WORDS-1];
        clk        = 1'b0;
        rst_n      = 1'b0;
        enable     = 1'b0;
        in_valid   = 1'b0;
        in_word    = '0;
        errors     = 0;
        frames_ref = 0;
        frames_hier = 0;
        started    = 1'b0;

        repeat (3) @(posedge clk);
        rst_n = 1'b1;
        repeat (2) @(posedge clk);
        enable = 1'b1;
        started = 1'b1;

        for (int f = 0; f < FRAMES; f++) begin
            for (int w = 0; w < WORDS; w++)
                frame_mem[w] = $urandom;
            for (int w = 0; w < WORDS; w++)
                send_word(frame_mem[w]);
            if (($urandom % 5) == 0) begin
                repeat (($urandom % 4) + 1) @(posedge clk);
                // Pause only between frames, never while send_word waits.
                enable = 1'b0;
                repeat (($urandom % 4) + 1) @(posedge clk);
                enable = 1'b1;
            end
        end
        repeat (WORDS * DATA_WIDTH * 3) @(posedge clk);
        #1;

        if (frames_ref == 0 || frames_ref !== frames_hier) begin
            $display("ERROR: frame count ref=%0d hier=%0d", frames_ref, frames_hier);
            errors++;
        end
        if (errors != 0) begin
            $display("FAIL: tb_serializer_hier_2to1 errors=%0d frames=%0d",
                     errors, frames_ref);
            $fatal(1);
        end
        $display("PASS: tb_serializer_hier_2to1 DATA_WIDTH=%0d WORDS=%0d LSB=%0d frames=%0d",
                 DATA_WIDTH, WORDS, LSB_FIRST, frames_ref);
        $finish;
    end

endmodule
