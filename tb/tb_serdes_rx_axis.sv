`timescale 1ns/1ps

module tb_serdes_rx_axis;

    localparam int DW    = 8;
    localparam int WORDS = 4;
    localparam int RATIO = DW * WORDS;

    logic clk;
    logic rst_n;
    logic enable;
    logic flush;
    logic align;
    logic serial_in;
    logic m_axis_tvalid;
    logic m_axis_tready;
    logic [DW-1:0] m_axis_tdata;
    logic m_axis_tlast;
    logic lock;
    logic lock_stale;
    logic frame_locked;
    logic frame_overflow;
    logic par_valid;
    logic sample_strobe;
    logic serial_data;
    logic serial_valid;
    logic signed [12:0] freq_trim;
    logic busy;
    logic clk_out;

    int errors;
    logic [RATIO-1:0] payload;

    always #5 clk = ~clk;

    serdes_rx_axis_digital #(
        .DATA_WIDTH(DW), .WORDS(WORDS), .LSB_FIRST(1'b1),
        .OVERSAMPLE(1), .CDR_BYPASS(1'b1), .ALIGN_MODE(0)
    ) dut (
        .rx_clk(clk), .rx_rst_n(rst_n), .rx_enable(enable),
        .rx_flush(flush), .rx_align(align), .rx_serial_in(serial_in),
        .rx_clear(1'b0),
        .m_axis_tvalid(m_axis_tvalid), .m_axis_tready(m_axis_tready),
        .m_axis_tdata(m_axis_tdata), .m_axis_tlast(m_axis_tlast),
        .rx_lock(lock), .rx_lock_stale(lock_stale),
        .rx_frame_locked(frame_locked), .rx_frame_overflow(frame_overflow),
        .rx_par_valid(par_valid), .rx_sample_strobe(sample_strobe),
        .rx_serial_data(serial_data), .rx_serial_valid(serial_valid),
        .rx_freq_trim(freq_trim), .rx_busy(busy), .rx_clk_out(clk_out)
    );

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

    initial begin
        logic [DW-1:0] data;
        logic last;
        clk           = 1'b0;
        rst_n         = 1'b0;
        enable        = 1'b0;
        flush         = 1'b0;
        align         = 1'b0;
        serial_in     = 1'b0;
        m_axis_tready = 1'b0;
        errors        = 0;
        payload       = 32'h89AB_CDEF;

        repeat (3) @(posedge clk);
        rst_n = 1'b1;
        repeat (3) @(posedge clk);

        // Start payload in the same cycle as enable, matching tb_cdr_bypass.
        // The receiver sampler adds one cycle before the deserializer sees
        // the captured bit; one extra edge drains the last bit.
        @(negedge clk);
        enable = 1'b1;
        for (int i = 0; i < RATIO; i++) begin
            serial_in = payload[i];
            @(posedge clk);
            #1;
        end
        @(posedge clk);
        #1;

        for (int w = 0; w < WORDS; w++) begin
            read_beat(data, last);
            if (data !== payload[w*DW +: DW]) begin
                $display("ERROR: AXIS word %0d got %h expected %h",
                         w, data, payload[w*DW +: DW]);
                errors++;
            end
            if (last !== (w == WORDS-1)) begin
                $display("ERROR: AXIS tlast wrong at word %0d", w);
                errors++;
            end
        end

        if (!lock || !frame_locked) begin
            $display("ERROR: lock/frame_locked not asserted");
            errors++;
        end
        if (frame_overflow) begin
            $display("ERROR: unexpected frame overflow");
            errors++;
        end

        if (errors != 0) begin
            $display("FAIL: tb_serdes_rx_axis errors=%0d", errors);
            $fatal(1);
        end
        $display("PASS: tb_serdes_rx_axis");
        $finish;
    end

endmodule
