`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// Migration smoke test for the legacy-compatible adapters.
//
// 1. Drive the adapter with an 8 x 32-bit frame and compare the serial stream
//    against the LSB-first frame order.
// 2. Feed the same stream into the deserializer adapter and compare all eight
//    output words.
// -----------------------------------------------------------------------------
module tb_compat_legacy;

    logic        clk = 0;
    logic        rst_n = 0;
    logic        ready = 0;
    logic        serial_in = 0;

    logic        ser_out;
    logic        ser_internal_finish;
    logic        ser_complete;
    logic [5:0]  ser_count;
    logic [3:0]  ser_sample_count;

    logic [31:0] par_in1, par_in2, par_in3, par_in4;
    logic [31:0] par_in5, par_in6, par_in7, par_in8;

    logic [31:0] des_out;
    logic [5:0]  des_count;
    logic [3:0]  des_sample_count;
    logic        des_internal_finish;
    logic        des_complete;
    logic [31:0] des_par1, des_par2, des_par3, des_par4;
    logic [31:0] des_par5, des_par6, des_par7, des_par8;

    logic [31:0] words [0:7];
    logic        expected [0:255];
    integer      i, w, b;
    integer      errors;

    always #5 clk = ~clk;

    serializer_unit_cell_1 u_ser (
        .CLK(clk),
        .RESET(rst_n),
        .READY(ready),
        .SERIAL_OUT(ser_out),
        .INTERNAL_FINISH(ser_internal_finish),
        .COMPLETE(ser_complete),
        .PAR_IN1(par_in1), .PAR_IN2(par_in2), .PAR_IN3(par_in3), .PAR_IN4(par_in4),
        .PAR_IN5(par_in5), .PAR_IN6(par_in6), .PAR_IN7(par_in7), .PAR_IN8(par_in8),
        .COUNT(ser_count),
        .SAMPLE_COUNT(ser_sample_count)
    );

    deserialiser_unit_cell_1 u_des (
        .CLK(clk),
        .RESET(rst_n),
        .READY(ready),
        .SERIAL_IN(serial_in),
        .PAR_IN1(des_par1), .PAR_IN2(des_par2), .PAR_IN3(des_par3), .PAR_IN4(des_par4),
        .PAR_IN5(des_par5), .PAR_IN6(des_par6), .PAR_IN7(des_par7), .PAR_IN8(des_par8),
        .OUT(des_out),
        .COUNT(des_count),
        .SAMPLE_COUNT(des_sample_count),
        .INTERNAL_FINISH(des_internal_finish),
        .COMPLETE(des_complete)
    );

    initial begin
        errors = 0;

        // Distinct, bit-covering patterns.
        words[0] = 32'h0123_4567;
        words[1] = 32'h89AB_CDEF;
        words[2] = 32'hFEDC_BA98;
        words[3] = 32'h7654_3210;
        words[4] = 32'hA5A5_5A5A;
        words[5] = 32'h0F0F_F0F0;
        words[6] = 32'h3333_CCCC;
        words[7] = 32'h8000_0001;

        par_in1 = words[0]; par_in2 = words[1]; par_in3 = words[2]; par_in4 = words[3];
        par_in5 = words[4]; par_in6 = words[5]; par_in7 = words[6]; par_in8 = words[7];

        for (w = 0; w < 8; w = w + 1)
            for (b = 0; b < 32; b = b + 1)
                expected[w*32 + b] = words[w][b];

        rst_n = 0;
        ready = 0;
        repeat (3) @(posedge clk);
        rst_n = 1;
        @(posedge clk);

        // Serializer adapter: READY starts a frame; the first bit is visible
        // one clock after the load edge.
        ready = 1;
        @(posedge clk);
        #1;
        for (i = 0; i < 256; i = i + 1) begin
            if (ser_out !== expected[i]) begin
                $display("FAIL: serializer bit %0d got %b expected %b", i, ser_out, expected[i]);
                errors = errors + 1;
            end
            if (i < 255) begin
                @(posedge clk);
                #1;
            end
        end
        if (!ser_complete) begin
            // Allow the completion pulse to land on the following clock;
            // the core and adapter both use registered frame_done.
            @(posedge clk);
            #1;
        end
        if (!ser_complete) begin
            $display("FAIL: serializer COMPLETE did not pulse at frame end");
            errors = errors + 1;
        end

        // Deserializer adapter: feed the exact expected stream, one bit per
        // clock, with READY high.
        rst_n = 0;
        ready = 0;
        serial_in = 0;
        repeat (2) @(posedge clk);
        rst_n = 1;
        @(posedge clk);

        ready = 1;
        for (i = 0; i < 256; i = i + 1) begin
            serial_in = expected[i];
            @(posedge clk);
            #1;
        end

        if ({des_par8, des_par7, des_par6, des_par5,
             des_par4, des_par3, des_par2, des_par1} !==
            {words[7], words[6], words[5], words[4],
             words[3], words[2], words[1], words[0]}) begin
            $display("FAIL: deserializer words %h %h %h %h %h %h %h %h",
                     des_par1, des_par2, des_par3, des_par4,
                     des_par5, des_par6, des_par7, des_par8);
            errors = errors + 1;
        end
        if (!des_complete) begin
            @(posedge clk);
            #1;
        end
        if (!des_complete) begin
            $display("FAIL: deserializer COMPLETE did not pulse at frame end");
            errors = errors + 1;
        end

        if (errors == 0)
            $display("PASS: tb_compat_legacy");
        else
            $display("FAIL: tb_compat_legacy errors=%0d", errors);
        $finish;
    end

endmodule
