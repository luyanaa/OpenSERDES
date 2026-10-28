`timescale 1ns/1ps

module tb_axis_rx_adapter;

    localparam int DW    = 16;
    localparam int WORDS = 4;

    logic clk;
    logic rst_n;
    logic clear;
    logic par_valid;
    logic [WORDS-1:0][DW-1:0] par_data;
    logic m_axis_tvalid;
    logic m_axis_tready;
    logic [DW-1:0] m_axis_tdata;
    logic m_axis_tlast;
    logic frame_overflow;

    int errors;

    always #5 clk = ~clk;

    axis_rx_adapter #(.DATA_WIDTH(DW), .WORDS(WORDS)) dut (
        .clk(clk), .rst_n(rst_n), .clear(clear),
        .par_valid(par_valid), .par_data(par_data),
        .m_axis_tvalid(m_axis_tvalid), .m_axis_tready(m_axis_tready),
        .m_axis_tdata(m_axis_tdata), .m_axis_tlast(m_axis_tlast),
        .frame_overflow(frame_overflow)
    );

    task automatic send_frame(input logic [WORDS-1:0][DW-1:0] data);
        begin
            @(negedge clk);
            par_data  = data;
            par_valid = 1'b1;
            @(posedge clk);
            #1;
            par_valid = 1'b0;
        end
    endtask

    task automatic read_beat(output logic [DW-1:0] data, output logic last);
        begin
            @(negedge clk);
            while (!m_axis_tvalid) @(negedge clk);
            data = m_axis_tdata;
            last = m_axis_tlast;
            m_axis_tready = 1'b1;
            @(posedge clk);
            #1;
            m_axis_tready = 1'b0;
        end
    endtask

    task automatic drain_frame(input logic [WORDS-1:0][DW-1:0] expected);
        logic [DW-1:0] data;
        logic last;
        begin
            for (int i = 0; i < WORDS; i++) begin
                read_beat(data, last);
                if (data !== expected[i]) begin
                    $display("ERROR: axis word %0d got %h expected %h",
                             i, data, expected[i]);
                    errors++;
                end
                if (last !== (i == WORDS-1)) begin
                    $display("ERROR: tlast wrong at word %0d", i);
                    errors++;
                end
            end
        end
    endtask

    task automatic drain_frame_bp(input logic [WORDS-1:0][DW-1:0] expected);
        logic [DW-1:0] data;
        logic last;
        int got;
        begin
            got = 0;
            while (got < WORDS) begin
                @(negedge clk);
                m_axis_tready = $urandom_range(0, 1);
                if (m_axis_tvalid && m_axis_tready) begin
                    data = m_axis_tdata;
                    last = m_axis_tlast;
                    if (data !== expected[got]) begin
                        $display("ERROR: bp word %0d got %h expected %h",
                                 got, data, expected[got]);
                        errors++;
                    end
                    if (last !== (got == WORDS-1)) begin
                        $display("ERROR: bp tlast wrong at word %0d", got);
                        errors++;
                    end
                    got++;
                end
            end
            @(negedge clk);
            m_axis_tready = 1'b0;
        end
    endtask

    initial begin
        logic [WORDS-1:0][DW-1:0] frame;
        clk           = 1'b0;
        rst_n         = 1'b0;
        clear         = 1'b0;
        par_valid     = 1'b0;
        par_data      = '0;
        m_axis_tready = 1'b0;
        errors        = 0;

        repeat (3) @(posedge clk);
        rst_n = 1'b1;
        repeat (2) @(posedge clk);

        for (int i = 0; i < WORDS; i++)
            frame[i] = 16'h1000 + i;
        send_frame(frame);
        drain_frame(frame);
        if (frame_overflow) begin
            $display("ERROR: overflow on a normal frame");
            errors++;
        end

        for (int i = 0; i < WORDS; i++)
            frame[i] = 16'h2000 + i;
        send_frame(frame);
        repeat (4) @(posedge clk);
        if (!m_axis_tvalid || m_axis_tdata !== frame[0]) begin
            $display("ERROR: backpressure did not hold first beat");
            errors++;
        end
        drain_frame(frame);

        send_frame(frame);
        repeat (2) @(posedge clk);
        send_frame(frame);
        if (!frame_overflow) begin
            $display("ERROR: pending frame overwrite not detected");
            errors++;
        end
        drain_frame(frame);

        // Same-cycle handoff: accept a new frame while consuming the last
        // beat of the previous one.  The new frame must not be dropped.
        @(negedge clk); clear = 1'b1; @(posedge clk); #1;
        @(negedge clk); clear = 1'b0;
        for (int i = 0; i < WORDS; i++)
            frame[i] = 16'h3000 + i;
        send_frame(frame);
        for (int i = 0; i < WORDS - 1; i++) begin
            logic [DW-1:0] old_data;
            logic old_last;
            read_beat(old_data, old_last);
            if (old_data !== frame[i]) begin
                $display("ERROR: handoff pre-beat %0d got %h expected %h",
                         i, old_data, frame[i]);
                errors++;
            end
            if (old_last !== 1'b0) begin
                $display("ERROR: handoff pre-beat tlast wrong at %0d", i);
                errors++;
            end
        end
        for (int i = 0; i < WORDS; i++)
            frame[i] = 16'h4000 + i;
        @(negedge clk);
        par_data      = frame;
        par_valid     = 1'b1;
        m_axis_tready = 1'b1;
        @(posedge clk);
        #1;
        par_valid     = 1'b0;
        m_axis_tready = 1'b0;
        if (frame_overflow) begin
            $display("ERROR: same-cycle handoff flagged overflow");
            errors++;
        end
        drain_frame(frame);
        // Randomized backpressure over a sequence of frames.
        for (int f = 0; f < 10; f++) begin
            for (int i = 0; i < WORDS; i++)
                frame[i] = 16'h5000 + f * 16'h100 + i;
            send_frame(frame);
            drain_frame_bp(frame);
            if (frame_overflow) begin
                $display("ERROR: overflow under random backpressure frame %0d", f);
                errors++;
            end
        end

        // Clear while a frame is pending: no beats may leak afterwards.
        for (int i = 0; i < WORDS; i++)
            frame[i] = 16'h6000 + i;
        send_frame(frame);
        @(negedge clk); clear = 1'b1;
        @(posedge clk); #1;
        @(negedge clk); clear = 1'b0;
        if (m_axis_tvalid) begin
            $display("ERROR: clear left a pending frame visible");
            errors++;
        end
        if (frame_overflow) begin
            $display("ERROR: clear did not clear overflow");
            errors++;
        end

        if (errors != 0) begin
            $display("FAIL: tb_axis_rx_adapter errors=%0d", errors);
            $fatal(1);
        end
        $display("PASS: tb_axis_rx_adapter");
        $finish;
    end

endmodule
