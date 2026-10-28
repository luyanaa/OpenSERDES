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
        @(negedge clk); clear = 1'b1; @(posedge clk); #1; clear = 1'b0;
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
