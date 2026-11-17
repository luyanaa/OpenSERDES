`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// serdes_multilane_top serial loopback testbench.
//
// Four lanes.  The TX side is fed one payload word per lane per serializer slot;
// the wrapper inserts a SYNC word, periodic markers and idles on the wire.  Each
// lane's serial output is delayed by a whole number of words (0/1/2/3 words),
// which is the lane-to-lane skew the deskew has to remove.
//
// RX clocks are the transmitter clock here (source-synchronous loopback); the
// per-lane async_fifo still exercises the CDC handshake, and the skew is applied
// on the serial line.
//
// Checks:
//   1. Every lane locks its word framing (rx_frame_locked) and the deskew
//      reports aligned + lane_locked after the first marker.
//   2. Exactly NW payload words come out, in source order, with all lanes
//      presenting the same word index on the same beat (lockstep).  A lane swap,
//      lost word, duplicated word or reordering fails the comparison.
//   3. No marker or realign errors: the skew is inside MAX_SKEW.
// -----------------------------------------------------------------------------
module tb_multilane_top;

    localparam int LANES = 4;
    localparam int DW    = 8;
    localparam int P     = 16;
    localparam int NW    = 200;
    localparam int DEPTH = 64;

    logic clk = 0, tx_rst_n = 0, tx_enable = 0;
    logic rx_rst_n = 0, rx_enable = 0, rx_flush = 0;
    logic core_rst_n = 0, core_enable = 0;

    logic                     tx_valid, tx_ready;
    logic [LANES-1:0][DW-1:0] tx_data;
    logic [LANES-1:0]         tx_serial_out;
    logic [LANES-1:0]         rx_clk;
    logic [LANES-1:0]         rx_serial_in;
    logic [LANES-1:0]         rx_frame_locked;

    logic [LANES-1:0][DW-1:0] rx_data;
    logic                     rx_valid;
    logic                     rx_ready = 1'b1;
    logic                     aligned, marker_ok, marker_error;
    logic [9:0]               realign_count;
    logic [LANES-1:0]         lane_locked;

    always #10 clk = ~clk;
    assign rx_clk = {LANES{clk}};

    serdes_multilane_top #(
        .LANES(LANES), .DATA_WIDTH(DW), .MARKER_PERIOD(P),
        .MAX_SKEW(8), .DESKEW_DEPTH(DEPTH), .FIFO_DEPTH(16),
        .TAG_WIDTH(4), .MARKER_TAG(4'hA), .IDLE_TAG(4'hF),
        .SYNC_WORD(8'hBC),
        .OVERSAMPLE(1), .CDR_MODE(2), .CDR_BYPASS(1'b1),
        .ALIGN_MODE(1)
    ) dut (
        .tx_clk(clk), .tx_rst_n(tx_rst_n), .tx_enable(tx_enable),
        .tx_valid(tx_valid), .tx_ready(tx_ready), .tx_data(tx_data),
        .tx_serial_out(tx_serial_out),
        .rx_clk(rx_clk), .rx_rst_n(rx_rst_n), .rx_enable(rx_enable),
        .rx_flush(rx_flush), .rx_serial_in(rx_serial_in),
        .rx_frame_locked(rx_frame_locked),
        .core_clk(clk), .core_rst_n(core_rst_n), .core_enable(core_enable),
        .rx_data(rx_data), .rx_valid(rx_valid), .rx_ready(rx_ready),
        .aligned(aligned), .marker_ok(marker_ok), .marker_error(marker_error),
        .realign_count(realign_count), .lane_locked(lane_locked)
    );

    // Per-lane serial delay: lane l is delayed by l whole words.
    generate
        for (genvar l = 0; l < LANES; l++) begin : g_delay
            localparam int DLY = l * DW;
            if (DLY == 0) begin : g_no_delay
                assign rx_serial_in[l] = tx_serial_out[l];
            end else begin : g_delay_line
                logic [DLY-1:0] sr_q;
                always_ff @(posedge clk or negedge tx_rst_n) begin
                    if (!tx_rst_n)
                        sr_q <= '0;
                    else
                        sr_q <= {sr_q[DLY-2:0], tx_serial_out[l]};
                end
                assign rx_serial_in[l] = sr_q[DLY-1];
            end
        end
    endgenerate

    // Payload values: 0x20..0x3F, so they never collide with the marker
    // (0xA?) or idle (0xF?) tags.
    logic [DW-1:0] exp [LANES*NW];
    initial begin
        for (int l = 0; l < LANES; l++)
            for (int i = 0; i < NW; i++)
                exp[l*NW + i] = 8'h20 |
                    (8'(i*5 + l*11 + (i >> 4)) & 8'h1F);
    end

    int tx_idx, rx_idx;
    assign tx_valid = tx_enable && (tx_idx < NW);

    always_comb begin
        for (int l = 0; l < LANES; l++)
            tx_data[l] = exp[l*NW + (tx_idx % NW)];
    end

    always @(posedge clk) begin
        if (!tx_rst_n)
            tx_idx <= 0;
        else if (tx_valid && tx_ready)
            tx_idx <= tx_idx + 1;
    end

    int errors, marker_oks, marker_errs;
    logic [DW-1:0] obs;
    logic [DW-1:0] want;

    always @(posedge clk) begin
        if (!core_rst_n) begin
            errors     <= 0;
            rx_idx     <= 0;
            marker_oks <= 0;
            marker_errs<= 0;
        end else begin
            if (marker_ok)
                marker_oks <= marker_oks + 1;
            if (marker_error)
                marker_errs <= marker_errs + 1;

            if (rx_valid && rx_ready) begin
                for (int l = 0; l < LANES; l++) begin
                    obs  = rx_data[l];
                    want = (rx_idx < NW) ? exp[l*NW + rx_idx] : 8'h00;
                    if (obs !== want) begin
                        $display("ERROR: beat %0d lane %0d got %02h expected %02h",
                                 rx_idx, l, obs, want);
                        errors <= errors + 1;
                    end
                end
                rx_idx <= rx_idx + 1;
            end
        end
    end

    initial begin
        for (int t = 0; t < 4; t++) @(posedge clk);
        tx_rst_n = 1'b1;
        rx_rst_n = 1'b1;
        core_rst_n = 1'b1;
        repeat (4) @(posedge clk);
        tx_enable = 1'b1;
        rx_enable = 1'b1;
        core_enable = 1'b1;

        for (int t = 0; t < 20000 && rx_idx < NW; t++)
            @(posedge clk);
        repeat (20) @(posedge clk);

        if (rx_idx != NW) begin
            $display("ERROR: received %0d of %0d payload words", rx_idx, NW);
            errors++;
        end
        if (aligned !== 1'b1) begin
            $display("ERROR: deskew not aligned");
            errors++;
        end
        if (realign_count != 0 || marker_errs != 0) begin
            $display("ERROR: realign_count=%0d marker_error=%0d",
                     realign_count, marker_errs);
            errors++;
        end
        if (marker_oks < 10) begin
            $display("ERROR: only %0d verified markers", marker_oks);
            errors++;
        end
        if (lane_locked !== {LANES{1'b1}}) begin
            $display("ERROR: lane_locked=%b", lane_locked);
            errors++;
        end
        if (rx_frame_locked !== {LANES{1'b1}}) begin
            $display("ERROR: rx_frame_locked=%b", rx_frame_locked);
            errors++;
        end

        if (errors == 0)
            $display("PASS: tb_multilane_top words=%0d markers=%0d frame_locked=%b",
                     rx_idx, marker_oks, rx_frame_locked);
        else
            $display("FAIL: tb_multilane_top errors=%0d words=%0d aligned=%b locked=%b",
                     errors, rx_idx, aligned, lane_locked);
        $finish;
    end

    initial begin
        #4000000;
        $display("FAIL: tb_multilane_top timeout tx=%0d rx=%0d errors=%0d",
                 tx_idx, rx_idx, errors);
        $finish;
    end

endmodule
