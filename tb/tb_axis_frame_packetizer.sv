`timescale 1ns/1ps

// Variable-length AXIS -> fixed WORDS-beat fragments -> variable-length AXIS.
// Frame lengths 1, WORDS-1, WORDS, 2*WORDS+3 and 17 exercise a single short
// fragment, an exactly full fragment, a two-fragment frame and a long frame.
// Random ready/valid stalls are injected on the internal link and on the
// depacketizer output.  A zero-length frame (tlast on the first beat) is sent
// at the end: it must raise frame_error, still produce one well-formed
// fragment, and be cleared by the next frame.
module tb_axis_frame_packetizer;

    localparam int DW    = 8;
    localparam int WORDS = 4;
    localparam int NLEN  = 5;
    localparam int MAXB  = 4096;

    logic clk, rst_n;
    logic clear;

    logic s_valid, s_ready, s_last;
    logic [DW-1:0] s_data;
    logic m_valid, m_ready, m_last, m_tuser;
    logic [DW-1:0] m_data;
    logic pkt_err;

    logic i_valid, i_ready, i_last, i_tuser;
    logic [DW-1:0] i_data;
    logic dep_valid, dep_ready, dep_last;
    logic [DW-1:0] dep_data;
    logic dep_err;

    int errors;
    int rnd;
    int ibeat;
    int iframes;
    int hidx;
    int frag_len_mon;
    int obeat;
    int oframes;

    int lens[NLEN];
    logic [DW-1:0] exp_data[MAXB];
    logic          exp_last[MAXB];
    int exp_n;

    always #5 clk = ~clk;

    // ~6% stall probability on the internal link and on the output.
    always @(posedge clk) rnd <= {rnd[30:0], rnd[30] ^ rnd[27]};

    assign i_valid = m_valid & rnd[3];
    assign m_ready = i_ready & rnd[3];
    assign i_data  = m_data;
    assign i_last  = m_last;
    assign i_tuser = m_tuser;

    assign dep_ready = rnd[7];

    axis_frame_packetizer #(.DATA_WIDTH(DW), .WORDS(WORDS)) u_pkt (
        .clk(clk), .rst_n(rst_n), .clear(clear),
        .s_axis_tvalid(s_valid), .s_axis_tready(s_ready),
        .s_axis_tdata(s_data), .s_axis_tlast(s_last),
        .m_axis_tvalid(m_valid), .m_axis_tready(m_ready),
        .m_axis_tdata(m_data), .m_axis_tlast(m_last),
        .m_axis_tuser(m_tuser), .frame_error(pkt_err)
    );

    axis_frame_depacketizer #(.DATA_WIDTH(DW), .WORDS(WORDS)) u_dep (
        .clk(clk), .rst_n(rst_n), .clear(clear),
        .s_axis_tvalid(i_valid), .s_axis_tready(i_ready),
        .s_axis_tdata(i_data), .s_axis_tlast(i_last),
        .m_axis_tvalid(dep_valid), .m_axis_tready(dep_ready),
        .m_axis_tdata(dep_data), .m_axis_tlast(dep_last),
        .frame_error(dep_err)
    );

    // Internal-link monitor: every frame is WORDS beats, tlast on the last,
    // header well formed, padding zero, and tuser carries last_frag.
    always @(posedge clk) begin
        if (rst_n && i_valid && i_ready) begin
            if (ibeat == 0) begin
                frag_len_mon = int'(i_data[4:0]);
                if (i_data[6:5] !== 2'b00) begin
                    $display("ERROR: header reserved bits %b", i_data[6:5]);
                    errors++;
                end
                if (frag_len_mon < 1 || frag_len_mon > WORDS-1) begin
                    $display("ERROR: header frag_len %0d", frag_len_mon);
                    errors++;
                end
            end else if (ibeat > frag_len_mon && i_data !== '0) begin
                $display("ERROR: padding beat %0d = %02h not zero", ibeat, i_data);
                errors++;
            end
            if (i_last !== (ibeat == WORDS-1)) begin
                $display("ERROR: internal tlast at beat %0d", ibeat);
                errors++;
            end
            if (i_tuser !== i_data[7] && ibeat == 0) begin
                $display("ERROR: tuser %b vs header last_frag %b", i_tuser, i_data[7]);
                errors++;
            end
            if (ibeat == WORDS-1) begin
                ibeat = 0;
                iframes++;
            end else begin
                ibeat++;
            end
        end
    end

    // Output scoreboard: byte-exact stream and tlast exactly at frame ends.
    always @(posedge clk) begin
        if (rst_n && dep_valid && dep_ready) begin
            if (obeat >= exp_n) begin
                $display("ERROR: extra output beat %02h", dep_data);
                errors++;
            end else begin
                if (dep_data !== exp_data[obeat]) begin
                    $display("ERROR: output byte %0d got %02h expected %02h",
                             obeat, dep_data, exp_data[obeat]);
                    errors++;
                end
                if (dep_last !== exp_last[obeat]) begin
                    $display("ERROR: output tlast wrong at byte %0d", obeat);
                    errors++;
                end
            end
            if (dep_last)
                oframes++;
            obeat++;
        end
    end

    task automatic send_byte(input logic [DW-1:0] data, input logic last);
        begin
            @(negedge clk);
            s_valid = 1'b1;
            s_data  = data;
            s_last  = last;
            while (!s_ready) @(negedge clk);
            @(posedge clk);
            #1;
            s_valid = 1'b0;
            s_last  = 1'b0;
            // Occasional source-side bubble.
            if (rnd[9]) @(negedge clk);
        end
    endtask

    initial begin
        int f, k, base;
        clk    = 1'b0;
        rst_n  = 1'b0;
        clear  = 1'b0;
        s_valid = 1'b0; s_data = '0; s_last = 1'b0;
        errors = 0; rnd = 32'h1234_5678;
        ibeat = 0; iframes = 0; obeat = 0; oframes = 0;
        exp_n = 0;

        lens[0] = 1;
        lens[1] = WORDS-1;
        lens[2] = WORDS;
        lens[3] = 2*WORDS + 3;
        lens[4] = 17;

        // Build the expected stream.
        for (f = 0; f < NLEN; f++) begin
            for (k = 0; k < lens[f]; k++) begin
                exp_data[exp_n] = DW'((f*31 + k) & 8'hFF);
                exp_last[exp_n] = (k == lens[f]-1);
                exp_n++;
            end
        end
        // Zero-length frame: one placeholder payload byte, then a normal frame.
        exp_data[exp_n] = 8'h5A; exp_last[exp_n] = 1'b1; exp_n++;
        exp_data[exp_n] = 8'h11; exp_last[exp_n] = 1'b0; exp_n++;
        exp_data[exp_n] = 8'h22; exp_last[exp_n] = 1'b1; exp_n++;

        repeat (4) @(posedge clk);
        rst_n = 1'b1;
        repeat (2) @(posedge clk);

        for (f = 0; f < NLEN; f++) begin
            base = (f*31) & 8'hFF;
            for (k = 0; k < lens[f]; k++)
                send_byte(DW'((base + k) & 8'hFF), (k == lens[f]-1));
        end

        // Zero-length frame: tlast on the first beat.
        send_byte(8'h5A, 1'b1);
        repeat (4) @(posedge clk);
        if (pkt_err !== 1'b1) begin
            $display("ERROR: zero-length frame not flagged (frame_error=%b)",
                     pkt_err);
            errors++;
        end
        // The next frame must clear the flag again.
        send_byte(8'h11, 1'b0);
        send_byte(8'h22, 1'b1);
        repeat (4) @(posedge clk);
        if (pkt_err !== 1'b0) begin
            $display("ERROR: frame_error not cleared (%b)", pkt_err);
            errors++;
        end

        // Drain: wait until the scoreboard has seen every expected byte.
        for (int t = 0; t < 20000 && obeat < exp_n; t++)
            @(posedge clk);
        repeat (20) @(posedge clk);

        if (obeat != exp_n) begin
            $display("ERROR: output beats %0d expected %0d", obeat, exp_n);
            errors++;
        end
        if (oframes != NLEN + 2) begin
            $display("ERROR: output frames %0d expected %0d", oframes, NLEN + 2);
            errors++;
        end
        if (iframes < NLEN) begin
            $display("ERROR: internal fragments %0d", iframes);
            errors++;
        end
        if (pkt_err !== 1'b0) begin
            $display("ERROR: packetizer frame_error high");
            errors++;
        end
        if (dep_err !== 1'b0) begin
            $display("ERROR: depacketizer frame_error high");
            errors++;
        end

        if (errors == 0)
            $display("PASS: tb_axis_frame_packetizer frames=%0d bytes=%0d frags=%0d",
                     oframes, obeat, iframes);
        else
            $display("FAIL: tb_axis_frame_packetizer errors=%0d", errors);
        $finish;
    end

    initial begin
        #2000000;
        $display("FAIL: tb_axis_frame_packetizer timeout errors=%0d", errors);
        $finish;
    end

endmodule
