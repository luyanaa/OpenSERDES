`timescale 1ns/1ps

// Datapath BIST primitive test.
//   Phase 1: prbs_word_gen -> prbs_word_check (streaming checker, direct).
//   Phase 2: prbs_word_gen -> serializer_stream -> deserializer; the received
//            comma+payload frame is compared against the accepted word queue.
module tb_serdes_bist_word;

    localparam int DW    = 8;
    localparam int PW    = 4;       // payload words
    localparam int FW    = PW + 1;  // comma + payload
    localparam int WIDTH = 7;

    logic clk;
    logic rst_n;
    logic start;
    logic clear;
    logic direct_mode;
    logic inject_word;

    logic gen_valid;
    logic gen_ready;
    logic gen_ready_serial;
    logic gen_last;
    logic [DW-1:0] gen_word;

    logic serial_line;
    logic serial_valid;

    logic [FW-1:0][DW-1:0] rx_words;
    logic rx_valid;

    logic chk_valid;
    logic chk_last;
    logic [DW-1:0] chk_word;
    logic error;
    logic [31:0] error_count;
    logic [31:0] frame_count;

    int errors;
    int serial_frames;
    int comma_errors;
    logic [DW-1:0] accepted [0:FW-1];
    int accepted_idx;

    always #5 clk = ~clk;

    prbs_word_gen #(
        .WIDTH(WIDTH), .DATA_WIDTH(DW), .WORDS(PW), .SEED('1), .COMMA(8'hBC)
    ) u_gen (
        .clk(clk), .rst_n(rst_n), .start(start), .clear(clear),
        .in_valid(gen_valid), .in_ready(gen_ready), .in_word(gen_word),
        .in_last(gen_last)
    );

    serializer_stream #(
        .DATA_WIDTH(DW), .WORDS(FW), .LSB_FIRST(1'b1)
    ) u_ser (
        .clk(clk), .rst_n(rst_n), .enable(1'b1),
        .in_valid(gen_valid & ~direct_mode), .in_ready(gen_ready_serial),
        .in_word(gen_word),
        .serial_out(serial_line), .serial_valid(serial_valid),
        .busy(), .frame_done()
    );

    deserializer #(
        .DATA_WIDTH(DW), .WORDS(FW), .LSB_FIRST(1'b1)
    ) u_des (
        .clk(clk), .rst_n(rst_n), .enable(1'b1), .align(1'b0), .flush(1'b0),
        .serial_in(serial_line), .serial_valid(serial_valid),
        .par_data(rx_words), .par_valid(rx_valid), .busy()
    );

    assign gen_ready  = direct_mode ? 1'b1 : gen_ready_serial;
    assign chk_valid  = direct_mode ? gen_valid : 1'b0;
    assign chk_last   = gen_last;
    assign chk_word   = gen_word ^ (inject_word ? 8'h01 : 8'h00);

    prbs_word_check #(
        .WIDTH(WIDTH), .DATA_WIDTH(DW), .WORDS(PW), .SEED('1), .COMMA(8'hBC),
        .SKIP_COMMA(1'b1)
    ) u_check (
        .clk(clk), .rst_n(rst_n), .clear(clear),
        .word_valid(chk_valid), .word_last(chk_last), .word_in(chk_word),
        .error(error), .error_count(error_count), .frame_count(frame_count)
    );

    // Record accepted words so the serialized frame can be compared against
    // the same sequence that entered the serializer.
    always @(posedge clk) begin
        if (gen_valid && gen_ready) begin
            accepted[accepted_idx] <= gen_word;
            if (accepted_idx == FW-1)
                accepted_idx <= 0;
            else
                accepted_idx <= accepted_idx + 1;
        end
        if (rx_valid) begin
            serial_frames <= serial_frames + 1;
            if (rx_words[0] !== 8'hBC) begin
                comma_errors <= comma_errors + 1;
            end
            // FW is fixed at 5 for this test; compare payload constants.
            if (rx_words[1] !== accepted[1]) begin
                $display("ERROR: serial word1 got %h expected %h",
                         rx_words[1], accepted[1]); errors <= errors + 1;
            end
            if (rx_words[2] !== accepted[2]) begin
                $display("ERROR: serial word2 got %h expected %h",
                         rx_words[2], accepted[2]); errors <= errors + 1;
            end
            if (rx_words[3] !== accepted[3]) begin
                $display("ERROR: serial word3 got %h expected %h",
                         rx_words[3], accepted[3]); errors <= errors + 1;
            end
            if (rx_words[4] !== accepted[4]) begin
                $display("ERROR: serial word4 got %h expected %h",
                         rx_words[4], accepted[4]); errors <= errors + 1;
            end
        end
    end

    task automatic clear_bist;
        begin
            @(negedge clk); clear = 1'b1;
            @(posedge clk); #1;
            @(negedge clk); clear = 1'b0;
        end
    endtask

    initial begin
        logic [31:0] err_before;
        clk          = 1'b0;
        rst_n        = 1'b0;
        start        = 1'b0;
        clear        = 1'b0;
        direct_mode  = 1'b1;
        inject_word  = 1'b0;
        errors       = 0;
        serial_frames = 0;
        comma_errors = 0;
        accepted_idx = 0;

        repeat (3) @(posedge clk);
        rst_n = 1'b1;
        repeat (2) @(posedge clk);

        // Phase 1: streaming checker against the generator.
        clear_bist();
        @(negedge clk); start = 1'b1;
        while (frame_count < 64) @(posedge clk);
        #1;
        if (error || error_count != 0) begin
            $display("ERROR: direct clean BIST error=%b count=%0d", error, error_count);
            errors++;
        end
        if (frame_count != 64) begin
            $display("ERROR: direct frame_count=%0d expected 64", frame_count);
            errors++;
        end

        // Checker error path: inject a bad bit on one accepted word.
        err_before = error_count;
        @(negedge clk); inject_word = 1'b1;
        repeat (2) @(posedge clk);
        @(negedge clk); inject_word = 1'b0;
        repeat (4) @(posedge clk);
        #1;
        if (!error || error_count <= err_before) begin
            $display("ERROR: injected word error not counted error=%b count=%0d",
                     error, error_count);
            errors++;
        end

        // Phase 2: serialize/deserialize the same frame stream.
        @(negedge clk); start = 1'b0; direct_mode = 1'b0;
        accepted_idx = 0;
        clear_bist();
        @(negedge clk); start = 1'b1;
        while (serial_frames < 8) @(posedge clk);
        @(negedge clk); start = 1'b0;
        #1;
        if (comma_errors != 0) begin
            $display("ERROR: comma errors=%0d", comma_errors);
            errors++;
        end

        // Clear must reset the streaming checker.
        clear_bist();
        #1;
        if (error || error_count != 0 || frame_count != 0) begin
            $display("ERROR: clear failed error=%b err=%0d frames=%0d",
                     error, error_count, frame_count);
            errors++;
        end

        if (errors != 0) begin
            $display("FAIL: tb_serdes_bist_word errors=%0d", errors);
            $fatal(1);
        end
        $display("PASS: tb_serdes_bist_word serial=%0d", serial_frames);
        $finish;
    end

endmodule
