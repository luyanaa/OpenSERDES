`timescale 1ns/1ps

// Store-and-forward CRC checker: a good frame is released only after its CRC
// verifies; a corrupted frame is dropped entirely (no payload reaches the
// output).  Also covers an over-length frame that exceeds MAX_FRAME_BEATS.
module tb_axis_crc_saf;
    logic clk = 0, rst_n = 0, enable = 1;
    logic s_valid = 0, s_last = 0, s_ready;
    logic [7:0] s_data = 0;
    logic ins_valid, ins_ready, ins_last, ins_user;
    logic [7:0] ins_data;
    logic [15:0] crc_word;
    logic chk_valid, chk_ready = 0, chk_last;
    logic [7:0] chk_data;
    logic crc_error, crc_ok, frame_drop;
    int errors = 0;

    always #5 clk = ~clk;

    axis_crc_inserter #(
        .DATA_WIDTH(8), .CRC_WIDTH(16), .POLY(16'h1021),
        .INIT(16'hFFFF), .LSB_FIRST(0), .REFLECT_OUT(0), .XOR_OUT(0)
    ) u_ins (
        .clk(clk), .rst_n(rst_n), .enable(enable),
        .s_axis_tvalid(s_valid), .s_axis_tready(s_ready),
        .s_axis_tdata(s_data), .s_axis_tlast(s_last),
        .m_axis_tvalid(ins_valid), .m_axis_tready(ins_ready),
        .m_axis_tdata(ins_data), .m_axis_tlast(ins_last),
        .m_axis_tuser(ins_user), .crc_word(crc_word)
    );

    // Small buffer (8 payload beats) to exercise the overflow/drop path.
    axis_crc_checker #(
        .DATA_WIDTH(8), .CRC_WIDTH(16), .POLY(16'h1021),
        .INIT(16'hFFFF), .LSB_FIRST(0), .REFLECT_OUT(0), .XOR_OUT(0),
        .STORE_AND_FORWARD(1), .MAX_FRAME_BEATS(8)
    ) u_chk (
        .clk(clk), .rst_n(rst_n), .enable(enable),
        .s_axis_tvalid(ins_valid), .s_axis_tready(ins_ready),
        .s_axis_tdata(ins_data_c), .s_axis_tlast(ins_last),
        .s_axis_tuser(ins_user),
        .m_axis_tvalid(chk_valid), .m_axis_tready(chk_ready),
        .m_axis_tdata(chk_data), .m_axis_tlast(chk_last),
        .crc_error(crc_error), .crc_ok(crc_ok), .frame_drop(frame_drop)
    );

    // Corrupt one payload beat between inserter and checker by flipping a bit
    // on a chosen beat index.  beat_count tracks inserter payload beats.
    int inj_at = -1, beat_count = 0;
    logic [7:0] ins_data_c;
    assign ins_data_c = (beat_count == inj_at) ? (ins_data ^ 8'h01) : ins_data;

    // Capture output beats.
    logic [7:0] out_bytes [0:255];
    int out_n = 0;
    logic saw_last = 0;
    always_ff @(posedge clk) begin
        if (chk_valid && chk_ready) begin
            out_bytes[out_n] <= chk_data;
            out_n <= out_n + 1;
            if (chk_last) saw_last <= 1'b1;
        end
    end

    task automatic send_byte(input logic [7:0] v, input logic last);
        begin
            @(negedge clk);
            s_valid = 1'b1; s_data = v; s_last = last;
            while (!s_ready) @(negedge clk);
            @(negedge clk);
            s_valid = 1'b0; s_last = 1'b0;
        end
    endtask

    task automatic drain_output(input int timeout);
        begin
            chk_ready = 1'b1;
            repeat (timeout) @(posedge clk);
            chk_ready = 1'b0;
        end
    endtask

    initial begin
        // Beat counter for corruption (payload beats only, at checker input).
        // We corrupt the data the *checker* sees, so count on s_fire of the
        // checker's slave port while it is in the payload phase.  Approximate:
        // count ins beats until tuser (payload last).
        fork
            begin
                forever begin
                    @(posedge clk);
                    if (ins_valid && ins_ready) begin
                        if (ins_user) beat_count <= 0;
                        else beat_count <= beat_count + 1;
                    end
                end
            end
        join_none

        repeat (3) @(posedge clk); rst_n = 1'b1; repeat (2) @(posedge clk);

        // ---- Frame 1: good 4-byte frame "ABCD" -------------------------------
        out_n = 0; saw_last = 0; inj_at = -1;
        send_byte(8'h41,0); send_byte(8'h42,0); send_byte(8'h43,0); send_byte(8'h44,1);
        // allow CRC check + drain
        repeat (6) @(posedge clk);
        drain_output(20);
        if (out_n != 4 || out_bytes[0]!=8'h41 || out_bytes[3]!=8'h44 || !saw_last) begin
            $display("FAIL good frame: out_n=%0d last=%0d b0=%02h", out_n, saw_last, out_bytes[0]);
            errors++;
        end
        if (!crc_ok_seen()) begin $display("FAIL no crc_ok on good frame"); errors++; end

        // ---- Frame 2: corrupted payload beat -> must be dropped --------------
        out_n = 0; saw_last = 0; inj_at = 1;   // corrupt 2nd payload beat
        send_byte(8'h55,0); send_byte(8'h66,0); send_byte(8'h77,0); send_byte(8'h88,1);
        repeat (6) @(posedge clk);
        drain_output(20);
        if (out_n != 0) begin
            $display("FAIL bad frame not dropped: out_n=%0d", out_n); errors++;
        end
        if (!saw_err_pulse()) begin $display("FAIL no crc_error on bad frame"); errors++; end
        inj_at = -1;

        // ---- Frame 3: over-length frame (12 beats > MAX_FRAME_BEATS=8) -------
        out_n = 0; saw_last = 0;
        for (int i = 0; i < 12; i++) send_byte(8'(i), (i==11));
        repeat (6) @(posedge clk);
        drain_output(30);
        if (out_n != 0) begin
            $display("FAIL over-length frame not dropped: out_n=%0d", out_n); errors++;
        end

        if (errors) $display("FAIL tb_axis_crc_saf errors=%0d", errors);
        else $display("PASS tb_axis_crc_saf: good delivered, bad dropped, overflow dropped");
        $finish;
    end

    // Track pulses (crc_ok/crc_error are 1-cycle) via sticky flags.
    logic ok_sticky = 0, err_sticky = 0;
    always_ff @(posedge clk) begin
        if (crc_ok) ok_sticky <= 1'b1;
        if (crc_error) err_sticky <= 1'b1;
    end
    function bit crc_ok_seen; return ok_sticky; endfunction
    function bit saw_err_pulse; return err_sticky; endfunction

    initial begin #20000; $display("FAIL timeout"); $finish; end
endmodule
