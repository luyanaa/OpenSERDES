`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// 8b/10b RX behaviour that a protocol layer (USB/PCIe style) depends on.
//
//   1. In-band error substitution: a received decode/disparity error must reach
//      the layer above as a definite broken symbol (a reserved K code), not as
//      a plausible-looking data byte that only the protocol CRC can catch.
//      Reference: PCIe 6.3.1.4 substitutes K28.4 for exactly this reason.
//   2. Lock watchdog on the comma detector: a receiver that never sees a comma
//      must say so instead of looking identical to one that is still searching.
//
// The decoders are *monitored*, not polled: `frame_valid` is a one-cycle pulse,
// so every check below runs off a captured copy.
// -----------------------------------------------------------------------------
module tb_8b10b_rx_protocol;

    logic clk = 0, rst_n = 0, enable = 0;
    always #5 clk = ~clk;

    int errors;

    localparam int SYMBOLS = 3;               // 2 payload + trailing comma
    localparam logic [7:0] SUBST_K = 8'h9C;   // K28.4
    localparam int PAYLOAD = SYMBOLS - 1;

    logic [SYMBOLS-1:0][9:0] par_data;
    logic                    par_valid, rd_init, rd_load;

    logic [PAYLOAD-1:0][7:0] payload, payload_off;
    logic [PAYLOAD-1:0]      charisk, charisk_off;
    logic                    code_error, code_error_off;
    logic                    substituted, idle_1, idle_2;
    logic                    frame_valid, frame_valid_off;

    /* verilator lint_off PINCONNECTEMPTY */
    pcs_8b10b_rx_stream #(
        .SYMBOLS(SYMBOLS), .DROP_LAST(1'b1),
        .SUBSTITUTE_ERROR(1'b1), .SUBSTITUTE_K(SUBST_K)
    ) u_sub (
        .clk(clk), .rst_n(rst_n), .enable(enable), .flush(1'b0),
        .par_data(par_data), .par_valid(par_valid),
        .rd_init(rd_init), .rd_load(rd_load),
        .payload(payload), .charisk(charisk),
        .frame_valid(frame_valid), .code_error(code_error),
        .symbol_substituted(substituted), .idle(idle_1)
    );

    pcs_8b10b_rx_stream #(
        .SYMBOLS(SYMBOLS), .DROP_LAST(1'b1), .SUBSTITUTE_ERROR(1'b0)
    ) u_off (
        .clk(clk), .rst_n(rst_n), .enable(enable), .flush(1'b0),
        .par_data(par_data), .par_valid(par_valid),
        .rd_init(rd_init), .rd_load(rd_load),
        .payload(payload_off), .charisk(charisk_off),
        .frame_valid(frame_valid_off), .code_error(code_error_off),
        .symbol_substituted(), .idle(idle_2)
    );
    /* verilator lint_on PINCONNECTEMPTY */

    // ---- captured results (frame_valid is a pulse) -------------------------
    logic [PAYLOAD-1:0][7:0] cap_pay, cap_pay_off;
    logic [PAYLOAD-1:0]      cap_k,   cap_k_off;
    int                      cap_n,   cap_n_off;
    bit                      cap_sub;

    always @(posedge clk) begin
        if (!rst_n) begin
            cap_n = 0; cap_n_off = 0; cap_sub = 1'b0;
        end else begin
            if (frame_valid) begin
                cap_pay <= payload;
                cap_k   <= charisk;
                cap_n   = cap_n + 1;
                cap_sub = cap_sub | substituted;
            end
            if (frame_valid_off) begin
                cap_pay_off <= payload_off;
                cap_k_off   <= charisk_off;
                cap_n_off   = cap_n_off + 1;
            end
        end
    end

    // ---- 8b/10b encoder, to build valid symbol groups ----------------------
    logic        enc_valid = 0;
    logic [7:0]  enc_data = 0;
    logic        enc_ctrl = 0;
    logic [9:0]  enc_code;
    logic        enc_code_valid, enc_code_error;

    pcs_8b10b_tx u_enc (
        .clk(clk), .rst_n(rst_n), .enable(1'b1), .valid(enc_valid),
        .data(enc_data), .ctrl(enc_ctrl), .code(enc_code),
        .code_valid(enc_code_valid), .code_error(enc_code_error)
    );

    task automatic encode(input logic [7:0] d, input logic c,
                          output logic [9:0] code);
        begin
            @(negedge clk);
            enc_data = d; enc_ctrl = c; enc_valid = 1'b1;
            @(posedge clk);
            @(negedge clk);
            enc_valid = 1'b0;
            @(posedge clk);
            @(negedge clk);
            code = enc_code;
        end
    endtask

    // Feed one symbol group and let the collector finish it.
    task automatic send_group(input logic [SYMBOLS-1:0][9:0] g);
        begin
            @(negedge clk);
            par_data  = g;
            par_valid = 1'b1;
            @(posedge clk);
            @(negedge clk);
            par_valid = 1'b0;
            // S_DEC + S_WAIT per symbol, then S_OUT.
            for (int i = 0; i < 8 * SYMBOLS + 16; i++) @(posedge clk);
        end
    endtask

    // A 10-bit word whose popcount is outside {4,5,6} cannot be valid 8b/10b.
    function automatic bit is_impossible(input logic [9:0] s);
        int ones;
        ones = 0;
        for (int i = 0; i < 10; i++) ones += s[i];
        is_impossible = (ones != 4) && (ones != 5) && (ones != 6);
    endfunction

    // ---- comma watchdog ---------------------------------------------------
    localparam int WD_TIMEOUT = 32;
    logic                 cwd_align, cwd_locked, cwd_seen, cwd_rd_plus;
    logic                 cwd_timeout, cwd_stuck;
    logic [WD_TIMEOUT:0]  cwd_dummy;

    pcs_8b10b_comma #(
        .LOCK_COMMAS(2), .RETRIGGER(1'b0), .LOCK_TIMEOUT(WD_TIMEOUT)
    ) u_cwd (
        .clk(clk), .rst_n(rst_n), .enable(enable), .flush(1'b0),
        .serial_in(1'b0), .serial_valid(1'b0), .slip_req(1'b0),
        .align(cwd_align), .locked(cwd_locked), .comma_seen(cwd_seen),
        .comma_rd_plus(cwd_rd_plus), .lock_timeout(cwd_timeout),
        .lock_stuck(cwd_stuck)
    );

    initial begin
        logic [9:0] d0, d1, comma;
        logic [SYMBOLS-1:0][9:0] group;
        int zero_ones;
        bit wd_seen;

        errors = 0;
        enable = 1'b0; par_valid = 1'b0; rd_init = 1'b0; rd_load = 1'b0;
        par_data = '0;

        repeat (3) @(posedge clk);
        rst_n  = 1'b1;
        enable = 1'b1;
        repeat (2) @(posedge clk);

        encode(8'h5A, 1'b0, d0);
        encode(8'hA5, 1'b0, d1);
        encode(8'hBC, 1'b1, comma);   // trailing comma (DROP_LAST)

        // ---- 1. clean group ------------------------------------------------
        // Symbol 0 is decoded first; the comma is last.
        group = {comma, d1, d0};
        send_group(group);
        if (cap_n != 1) begin
            $display("ERROR: clean group produced %0d frames", cap_n);
            errors++;
        end else if (cap_pay[0] !== 8'h5A || cap_pay[1] !== 8'hA5) begin
            $display("ERROR: clean payload %02h/%02h expected 5A/A5",
                     cap_pay[0], cap_pay[1]);
            errors++;
        end
        if (|cap_k || cap_sub) begin
            $display("ERROR: clean group flagged k=%b sub=%b", cap_k, cap_sub);
            errors++;
        end

        // ---- 2. corrupted symbol -------------------------------------------
        group[0] = d0 ^ 10'b00_0001_1110;
        if (!is_impossible(group[0])) begin
            $display("ERROR: injected symbol is still decodable");
            errors++;
        end
        send_group(group);
        if (cap_n != 2) begin
            $display("ERROR: corrupted group produced %0d frames (expected 2)",
                     cap_n);
            errors++;
        end else if (cap_pay[0] !== SUBST_K || cap_k[0] !== 1'b1) begin
            $display("ERROR: corrupted symbol not substituted (%02h k=%b)",
                     cap_pay[0], cap_k[0]);
            errors++;
        end
        if (!cap_sub) begin
            $display("ERROR: symbol_substituted never pulsed"); errors++;
        end
        // The pre-substitution behaviour must differ: the byte is forwarded as
        // decoded (whatever garbage the table produced), not as the K code.
        if (cap_n_off == cap_n && cap_pay_off[0] === SUBST_K && cap_k_off[0])
            begin
            $display("ERROR: SUBSTITUTE_ERROR=0 substituted too"); errors++;
        end

        // ---- 3. comma watchdog ---------------------------------------------
        // serial_valid is tied low, so no comma can ever arrive.
        for (int i = 0; i < 4 * WD_TIMEOUT; i++) begin
            @(posedge clk);
            if (cwd_timeout) wd_seen = 1'b1;
        end
        if (!wd_seen) begin
            $display("ERROR: comma watchdog never pulsed"); errors++;
        end
        if (!cwd_stuck) begin
            $display("ERROR: comma watchdog did not latch stuck"); errors++;
        end
        if (cwd_locked) begin
            $display("ERROR: comma detector locked without a comma"); errors++;
        end

        if (errors == 0)
            $display("PASS: tb_8b10b_rx_protocol substitution=%02h, comma watchdog ok",
                     SUBST_K);
        else
            $display("FAIL: tb_8b10b_rx_protocol errors=%0d", errors);
        $finish;
    end

    initial begin
        #500000;
        $display("FAIL: tb_8b10b_rx_protocol timeout errors=%0d", errors);
        $finish;
    end

endmodule
