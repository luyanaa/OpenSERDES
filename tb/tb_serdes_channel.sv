`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// Digital top-level + behavioral channel test.
//
// This test verifies:
//   - TX serializer -> channel -> CDR recovered serial stream
//   - CDR lock and sample_strobe
//   - Deserializer frame completion (par_valid)
//
// It deliberately does NOT require a particular parallel frame alignment.
// The deserializer has an align input for a future comma/training detector;
// parallel frame reconstruction is covered by tb_deserializer with an exact
// bit boundary.  Requiring a fixed alignment here would hide the fact that a
// real link still needs comma/training alignment.
// -----------------------------------------------------------------------------
module tb_serdes_channel;

    localparam int DATA_WIDTH  = 8;
    localparam int WORDS       = 4;
    localparam int RATIO       = DATA_WIDTH * WORDS;
    localparam int OVERSAMPLE  = 4;
    localparam int SYNC_STAGES = 2;
    localparam int LOCK_THRESH = 8;

    logic tx_clk;
    logic rx_clk;
    logic tx_rst_n;
    logic rx_rst_n;
    logic tx_enable;
    logic tx_load;
    logic [WORDS-1:0][DATA_WIDTH-1:0] tx_data;
    logic [WORDS-1:0][DATA_WIDTH-1:0] rx_data;

    logic tx_ready;
    logic tx_serial_out;
    logic tx_serial_valid;
    logic tx_busy;
    logic tx_frame_done;

    logic rx_par_valid;
    logic rx_busy;
    logic rx_lock;
    logic rx_sample_strobe;
    logic rx_clk_out;

    logic rx_flush;
    logic rx_align;
    logic channel_out;
    logic channel_valid;

    int errors;
    int par_valid_count;

    always #5 rx_clk = ~rx_clk;   // 100 MHz oversampling clock
    always #20 tx_clk = ~tx_clk; // 25 MHz bit clock (4x oversampling)

    serdes_digital_top #(
        .DATA_WIDTH(DATA_WIDTH),
        .WORDS(WORDS),
        .LSB_FIRST(1'b1),
        .OVERSAMPLE(OVERSAMPLE),
        .SYNC_STAGES(SYNC_STAGES),
        .LOCK_THRESH(LOCK_THRESH)
    ) dut (
        .tx_clk(tx_clk),
        .tx_rst_n(tx_rst_n),
        .tx_enable(tx_enable),
        .tx_load(tx_load),
        .tx_par_data(tx_data),
        .tx_ready(tx_ready),
        .tx_serial_out(tx_serial_out),
        .tx_serial_valid(tx_serial_valid),
        .tx_busy(tx_busy),
        .tx_frame_done(tx_frame_done),

        .rx_clk(rx_clk),
        .rx_rst_n(rx_rst_n),
        .rx_enable(1'b1),
        .rx_flush(rx_flush),
        .rx_align(rx_align),
        .rx_serial_in(channel_out),
        .rx_par_data(rx_data),
        .rx_par_valid(rx_par_valid),
        .rx_busy(rx_busy),
        .rx_lock(rx_lock),
        .rx_sample_strobe(rx_sample_strobe),
        .rx_clk_out(rx_clk_out)
    );

    channel_model #(
        .DELAY_CYCLES(0),
        .BIT_ERROR_PERIOD(0),
        .INVERT(1'b0)
    ) channel (
        .clk(tx_clk),
        .rst_n(tx_rst_n),
        .serial_in(tx_serial_out),
        .serial_valid(tx_serial_valid),
        .serial_out(channel_out),
        .serial_valid_out(channel_valid)
    );

    always @(posedge rx_clk) begin
        if (rx_par_valid)
            par_valid_count <= par_valid_count + 1;
    end

    task automatic load_frame(input logic [RATIO-1:0] data);
        begin
            while (!tx_ready) @(negedge tx_clk);
            @(negedge tx_clk);
            for (int w = 0; w < WORDS; w++)
                tx_data[w] = data[w*DATA_WIDTH +: DATA_WIDTH];
            tx_load = 1'b1;
            @(posedge tx_clk);
            #1;
            tx_load = 1'b0;
        end
    endtask

    // Collect 64 recovered serial bits and search for the 32-bit frame.
    task automatic check_serial_frame(input logic [RATIO-1:0] expected);
        logic [63:0] bits;
        int idx;
        int found;
        begin
            bits  = '0;
            idx   = 0;
            found = 0;

            while (idx < 64) begin
                @(posedge rx_clk);
                #1;
                if (rx_sample_strobe) begin
                    bits[idx] = channel_out;
                    idx++;
                end
            end

            for (int k = 0; k <= 32; k++) begin
                if (bits[k +: RATIO] == expected)
                    found = 1;
            end

            if (!found) begin
                $display("ERROR: recovered serial stream does not contain expected frame %h",
                         expected);
                $display("       captured bits = %h", bits);
                errors++;
            end
        end
    endtask

    initial begin
        tx_clk    = 1'b0;
        rx_clk    = 1'b0;
        tx_rst_n  = 1'b0;
        rx_rst_n  = 1'b0;
        tx_enable = 1'b0;
        tx_load   = 1'b0;
        rx_flush  = 1'b0;
        rx_align  = 1'b0;
        tx_data   = '0;
        errors    = 0;
        par_valid_count = 0;

        repeat (4) @(posedge rx_clk);
        tx_rst_n = 1'b1;
        rx_rst_n = 1'b1;
        wait (dut.u_tx.tx_rst_n_sync);
        wait (dut.u_rx.rx_rst_n_sync);
        repeat (2) @(posedge tx_clk);
        repeat (2) @(posedge rx_clk);
        tx_enable = 1'b1;

        // Preamble: alternating data gives the CDR plenty of edges.
        load_frame(32'hAAAA_AAAA);
        wait (tx_frame_done);
        @(posedge rx_clk);

        // Wait for CDR lock.
        begin : wait_lock
            int timeout;
            timeout = 0;
            while (!rx_lock && timeout < 4096) begin
                @(posedge rx_clk);
                #1;
                timeout++;
            end
            if (!rx_lock) begin
                $display("ERROR: CDR did not lock");
                $fatal(1);
            end
        end

        // Real frame.  The serial stream is checked with a 64-bit search
        // window; no fixed parallel frame alignment is assumed.
        load_frame(32'h0123_4567);
        check_serial_frame(32'h0123_4567);

        // A second frame checks repeatability.
        load_frame(32'h89AB_CDEF);
        check_serial_frame(32'h89AB_CDEF);

        if (par_valid_count == 0) begin
            $display("ERROR: deserializer never produced par_valid");
            errors++;
        end

        if (errors != 0) begin
            $display("FAIL: tb_serdes_channel errors=%0d", errors);
            $fatal(1);
        end
        $display("PASS: tb_serdes_channel par_valid_count=%0d", par_valid_count);
        $finish;
    end

endmodule
