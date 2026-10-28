`timescale 1ns/1ps

module tb_serdes_tx_axis;

    localparam int DW    = 8;
    localparam int WORDS = 4;
    localparam int RATIO = DW * WORDS;

    logic clk;
    logic rst_n;
    logic enable;
    logic clear;
    logic s_axis_tvalid;
    logic s_axis_tready;
    logic [DW-1:0] s_axis_tdata;
    logic s_axis_tlast;
    logic serial_out;
    logic serial_valid;
    logic busy;
    logic frame_done;
    logic frame_error;

    int errors;
    int done_count;
    logic [RATIO-1:0] payload;

    always #5 clk = ~clk;

    serdes_tx_axis_digital #(.DATA_WIDTH(DW), .WORDS(WORDS), .LSB_FIRST(1'b1)) dut (
        .tx_clk(clk), .tx_rst_n(rst_n), .tx_enable(enable), .tx_clear(clear),
        .s_axis_tvalid(s_axis_tvalid), .s_axis_tready(s_axis_tready),
        .s_axis_tdata(s_axis_tdata), .s_axis_tlast(s_axis_tlast),
        .tx_serial_out(serial_out), .tx_serial_valid(serial_valid),
        .tx_busy(busy), .tx_frame_done(frame_done), .tx_frame_error(frame_error)
    );

    always @(posedge clk) if (frame_done) done_count <= done_count + 1;

    task automatic send_word(input logic [DW-1:0] data, input logic last);
        begin
            @(negedge clk);
            s_axis_tvalid = 1'b1;
            s_axis_tdata  = data;
            s_axis_tlast  = last;
            while (!s_axis_tready) @(negedge clk);
            @(posedge clk);
            #1;
            s_axis_tvalid = 1'b0;
            s_axis_tlast  = 1'b0;
        end
    endtask

    initial begin
        clk           = 1'b0;
        rst_n         = 1'b0;
        enable        = 1'b0;
        clear         = 1'b0;
        s_axis_tvalid = 1'b0;
        s_axis_tdata  = '0;
        s_axis_tlast  = 1'b0;
        errors        = 0;
        done_count    = 0;
        payload       = 32'h0123_4567;

        repeat (3) @(posedge clk);
        rst_n = 1'b1;
        repeat (2) @(posedge clk);
        enable = 1'b1;

        for (int w = 0; w < WORDS; w++)
            send_word(payload[w*DW +: DW], (w == WORDS-1));

        // Wait for serialization to start and check all bits.
        while (!serial_valid) @(posedge clk);
        for (int i = 0; i < RATIO; i++) begin
            if (!serial_valid) begin
                $display("ERROR: serial_valid low at bit %0d", i);
                errors++;
            end
            if (serial_out !== payload[i]) begin
                $display("ERROR: bit %0d got %0b expected %0b",
                         i, serial_out, payload[i]);
                errors++;
            end
            @(posedge clk);
            #1;
        end
        if (!frame_done) begin
            $display("ERROR: frame_done missing");
            errors++;
        end
        if (frame_error) begin
            $display("ERROR: frame_error asserted");
            errors++;
        end
        @(posedge clk);
        #1;
        if (done_count != 1) begin
            $display("ERROR: frame_done count %0d", done_count);
            errors++;
        end

        if (errors != 0) begin
            $display("FAIL: tb_serdes_tx_axis errors=%0d", errors);
            $fatal(1);
        end
        $display("PASS: tb_serdes_tx_axis");
        $finish;
    end

endmodule
