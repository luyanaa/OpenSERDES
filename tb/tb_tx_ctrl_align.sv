`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// TX control alignment: a direct control input must not overtake the data.
//
// Reference: LitePCIe PR #250 (ECP5 DCU).  `FFC_EI_EN` is a direct transceiver
// input, while the EIOS is still in the TX pipeline, so the electrical-idle
// request cut the ordered set on the line.  Their test is the shape reproduced
// here:
//
//   delay 0 -> 0 symbols of the EIOS reach the line
//   delay 1 -> 0 symbols
//   delay 2 -> 4 symbols (COM + 3 IDL), i.e. the whole ordered set
//
// Our pipeline is modelled as a 4-cycle word pipeline: a word presented on
// `word_in` is emitted 4 cycles later, and the control *inhibits* emission
// while it is high.  A request at cycle N with delay D starts inhibiting at
// N+D.  So the number of the WORDS presented that actually reach the line is
//   D <  PIPE : the tail is cut (fewer words than presented)
//   D >= PIPE : every presented word gets out before the control lands.
// The test sweeps D and asserts that monotonicity plus the full-delivery case:
// that is the "align the control with the data, or it truncates the frame"
// property PR #250 found, and it is what a `tx_idle_delay` / `tx_clear` must
// decide on purpose.
// -----------------------------------------------------------------------------
module tb_tx_ctrl_align;

    logic clk = 0, rst_n = 0;
    always #5 clk = ~clk;

    int errors;

    localparam int PIPE  = 4;    // word pipeline depth (cycles)
    localparam int WORDS = 8;    // ordered-set words to send

    // ---- pipeline model ---------------------------------------------------
    logic [7:0]  word_in = 8'h00;
    logic        word_valid = 1'b0;
    logic [PIPE-1:0][7:0] pipe_q;
    logic [PIPE-1:0]      pipe_v;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            pipe_q <= '0;
            pipe_v <= '0;
        end else begin
            pipe_q <= {pipe_q[PIPE-2:0], word_in};
            pipe_v <= {pipe_v[PIPE-2:0], word_valid};
        end
    end

    logic [7:0] line_word;
    logic       line_valid;
    assign line_word  = pipe_q[PIPE-1];
    assign line_valid = pipe_v[PIPE-1];

    // ---- control alignment sweeps -----------------------------------------
    localparam int MAXD = 8;

    logic [MAXD-1:0][0:0] ctrl_in_bus;
    logic [MAXD-1:0][0:0] ctrl_out_bus;

    generate
        for (genvar d = 0; d < MAXD; d++) begin : g_sweep
            tx_ctrl_align #(.DELAY_CYCLES(d)) u_align (
                .clk(clk), .rst_n(rst_n),
                .ctrl_in(ctrl_in_bus[d]), .ctrl_out(ctrl_out_bus[d])
            );
        end
    endgenerate

    // Count the ordered-set words that actually reach the line, per delay.
    int  symbols_on_line [MAXD];
    bit  monitor_en = 1'b0;

    // Concurrent line monitor: counts a word as delivered when it is valid and
    // the aligned control for that delay has not inhibited the transmitter.
    always @(posedge clk) begin
        if (rst_n && monitor_en && line_valid)
            for (int d = 0; d < MAXD; d++)
                if (!ctrl_out_bus[d])
                    symbols_on_line[d] <= symbols_on_line[d] + 1;
    end

    initial begin
        errors = 0;
        ctrl_in_bus = '0;
        for (int d = 0; d < MAXD; d++) symbols_on_line[d] = 0;

        repeat (3) @(posedge clk);
        rst_n = 1'b1;
        repeat (2) @(posedge clk);

        monitor_en = 1'b1;

        // Drive the ordered set (COM then 7 IDL words) and assert the control
        // right after the last word is presented -- the LTSSM behaviour.
        for (int k = 0; k < WORDS; k++) begin
            @(negedge clk);
            word_in    = (k == 0) ? 8'hBC : 8'h1C;   // COM, then IDL
            word_valid = 1'b1;
        end
        @(negedge clk);
        word_valid = 1'b0;
        // Assert every delay's control input on the same cycle.
        for (int d = 0; d < MAXD; d++) ctrl_in_bus[d] = 1'b1;

        // Drain the pipeline.
        for (int t = 0; t < 4 * PIPE + 4 * WORDS; t++) @(posedge clk);
        monitor_en = 1'b0;

        for (int d = 0; d < MAXD; d++)
            $display("INFO: delay %0d delivered %0d of %0d words",
                     d, symbols_on_line[d], WORDS);

        $display("INFO: symbols on the line per delay: %0d %0d %0d %0d %0d %0d %0d %0d",
                 symbols_on_line[0], symbols_on_line[1], symbols_on_line[2],
                 symbols_on_line[3], symbols_on_line[4], symbols_on_line[5],
                 symbols_on_line[6], symbols_on_line[7]);

        // A short delay truncates the tail; the delivered count must rise
        // monotonically with the delay and saturate at full delivery.
        for (int d = 1; d < MAXD; d++)
            if (symbols_on_line[d] < symbols_on_line[d - 1]) begin
                $display("ERROR: delay %0d delivered %0d < delay %0d (%0d)",
                         d, symbols_on_line[d], d - 1, symbols_on_line[d - 1]);
                errors++;
            end
        for (int d = 0; d < PIPE; d++)
            if (symbols_on_line[d] >= WORDS) begin
                $display("ERROR: delay %0d still delivered everything (%0d)",
                         d, symbols_on_line[d]);
                errors++;
            end
        for (int d = PIPE; d < MAXD; d++)
            if (symbols_on_line[d] != WORDS) begin
                $display("ERROR: delay %0d delivered %0d of %0d",
                         d, symbols_on_line[d], WORDS);
                errors++;
            end

        if (errors == 0)
            $display("PASS: tb_tx_ctrl_align truncates below %0d, aligned at %0d+",
                     PIPE, PIPE);
        else
            $display("FAIL: tb_tx_ctrl_align errors=%0d", errors);
        $finish;
    end

    initial begin
        #200000;
        $display("FAIL: tb_tx_ctrl_align timeout");
        $finish;
    end

endmodule
