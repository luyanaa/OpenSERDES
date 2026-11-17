`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// Multi-lane AXIS loopback: bonder -> serdes_multilane_top -> debonder.
//
// This is the SoC-facing shape of a bonded link: one wide AXI4-Stream beat per
// multi-lane word, turned into the lockstep stripe the deskew produces and back
// again.  The serial side is a loopback with per-lane delays of 0/1/2/3 words,
// i.e. the skew the deskew has to remove.
//
// Checks:
//   1. Every wide beat leaving the debonder equals the beat that entered the
//      bonder: fixed lane order, no lane swap, no reordering, no gaps.
//   2. Exactly NW beats arrive.
//   3. link_ok rises after the deskew locks, every lane reports locked and
//      framed, and no lane_fault bit latches.
//   4. (unit) A second debonder instance is driven with link_ok low: it must
//      suppress tvalid, keep accepting (so the core drains) and count the
//      dropped beats.
// -----------------------------------------------------------------------------
module tb_multilane_axis;

    localparam int LANES = 4;
    localparam int DW    = 8;
    localparam int WIDE  = LANES * DW;
    localparam int P     = 16;     // payload words between markers
    localparam int NW    = 240;
    localparam int DEPTH = 64;

    logic clk = 0, rst_n = 0, enable = 0;

    // SoC side.
    logic [WIDE-1:0] sx_tdata;
    logic            sx_tvalid, sx_tready;
    logic [WIDE-1:0] mx_tdata;
    logic            mx_tvalid;
    logic            mx_tready;

    // Bonding layer.
    logic [LANES-1:0][DW-1:0] tx_lane_data, rx_lane_data;
    logic                    tx_lane_valid, tx_lane_ready;

    // Serial side.
    logic [LANES-1:0] tx_serial_out, rx_serial_in, rx_clk;
    logic [LANES-1:0] rx_frame_locked;

    logic             rx_valid;
    logic             rx_ready;
    logic             aligned, marker_ok, marker_error;
    logic [9:0]       realign_count;
    logic [LANES-1:0] lane_locked, lane_fault;
    logic             link_ok;
    logic [15:0]      underflow;

    always #10 clk = ~clk;
    assign rx_clk    = {LANES{clk}};
    assign mx_tready = 1'b1;          // SoC always accepts

    axis_lane_bonder #(.LANES(LANES), .DATA_WIDTH(DW)) u_bonder (
        .clk(clk), .rst_n(rst_n), .enable(enable),
        .s_axis_tdata(sx_tdata), .s_axis_tvalid(sx_tvalid),
        .s_axis_tready(sx_tready),
        .m_lane_data(tx_lane_data), .m_lane_valid(tx_lane_valid),
        .m_lane_ready(tx_lane_ready)
    );

    serdes_multilane_top #(
        .LANES(LANES), .DATA_WIDTH(DW), .MARKER_PERIOD(P),
        .MAX_SKEW(8), .DESKEW_DEPTH(DEPTH), .FIFO_DEPTH(16),
        .TAG_WIDTH(4), .MARKER_TAG(4'hA), .IDLE_TAG(4'hF),
        .SYNC_WORD(8'hBC),
        .OVERSAMPLE(1), .CDR_MODE(2), .CDR_BYPASS(1'b1),
        .ALIGN_MODE(1)
    ) dut (
        .tx_clk(clk), .tx_rst_n(rst_n), .tx_enable(enable),
        .tx_valid(tx_lane_valid), .tx_ready(tx_lane_ready),
        .tx_data(tx_lane_data), .tx_serial_out(tx_serial_out),
        .rx_clk(rx_clk), .rx_rst_n(rst_n), .rx_enable(enable),
        .rx_flush(1'b0), .rx_serial_in(rx_serial_in),
        .rx_frame_locked(rx_frame_locked),
        .core_clk(clk), .core_rst_n(rst_n), .core_enable(enable),
        .rx_data(rx_lane_data), .rx_valid(rx_valid), .rx_ready(rx_ready),
        .aligned(aligned), .marker_ok(marker_ok),
        .marker_error(marker_error), .realign_count(realign_count),
        .lane_locked(lane_locked), .lane_fault(lane_fault),
        .link_ok(link_ok)
    );

    axis_lane_debonder #(.LANES(LANES), .DATA_WIDTH(DW)) u_debonder (
        .clk(clk), .rst_n(rst_n), .enable(enable),
        .s_lane_data(rx_lane_data), .s_lane_valid(rx_valid),
        .s_lane_ready(rx_ready), .link_ok(link_ok),
        .m_axis_tdata(mx_tdata), .m_axis_tvalid(mx_tvalid),
        .m_axis_tready(mx_tready), .underflow(underflow)
    );

    // Per-lane serial delay: lane l is delayed by l whole words.
    generate
        for (genvar l = 0; l < LANES; l++) begin : g_delay
            localparam int DLY = l * DW;
            if (DLY == 0) begin : g_no_delay
                assign rx_serial_in[l] = tx_serial_out[l];
            end else begin : g_delay_line
                logic [DLY-1:0] sr_q;
                always_ff @(posedge clk or negedge rst_n) begin
                    if (!rst_n)
                        sr_q <= '0;
                    else
                        sr_q <= {sr_q[DLY-2:0], tx_serial_out[l]};
                end
                assign rx_serial_in[l] = sr_q[DLY-1];
            end
        end
    endgenerate

    // Payload generator: 0x20..0x3F, never a reserved ordered-set tag.
    logic [DW-1:0] exp_word [LANES*NW];
    initial begin
        for (int l = 0; l < LANES; l++)
            for (int i = 0; i < NW; i++)
                exp_word[l*NW + i] = 8'h20 |
                    (8'(i * 7 + l * 13 + (i >> 3)) & 8'h1F);
    end

    int tx_idx;
    always_comb begin
        sx_tvalid = enable && (tx_idx < NW);
        sx_tdata  = '0;
        for (int l = 0; l < LANES; l++)
            sx_tdata[l*DW +: DW] = exp_word[l*NW + (tx_idx % NW)];
    end

    always @(posedge clk) begin
        if (!rst_n)
            tx_idx <= 0;
        else if (sx_tvalid && sx_tready)
            tx_idx <= tx_idx + 1;
    end

    int              errors, rx_idx, marker_oks, marker_errs, lane_swap;
    logic            loopback_ok, loopback_aligned;
    logic [WIDE-1:0] want;

    always_comb begin
        want = '0;
        for (int l = 0; l < LANES; l++)
            want[l*DW +: DW] = (rx_idx < NW) ? exp_word[l*NW + rx_idx] : 8'h00;
    end

    always @(posedge clk) begin
        if (!rst_n) begin
            errors <= 0; rx_idx <= 0; marker_oks <= 0; marker_errs <= 0;
            lane_swap <= 0;
        end else if (enable) begin
            if (marker_ok)    marker_oks  <= marker_oks + 1;
            if (marker_error) marker_errs <= marker_errs + 1;

            if (mx_tvalid && mx_tready) begin
                if (mx_tdata !== want) begin
                    $display("ERROR: beat %0d got %h expected %h",
                             rx_idx, mx_tdata, want);
                    for (int l = 0; l < LANES; l++)
                        if (mx_tdata[l*DW +: DW] ===
                            exp_word[l*NW + rx_idx] && l != l)
                            lane_swap <= lane_swap + 1;
                    errors <= errors + 1;
                end
                rx_idx <= rx_idx + 1;
            end
        end
    end

    // -------------------------------------------------------------------------
    // Unit test of the debonder's link_ok gating, independent of the serial
    // link: drive it with lane data while link_ok is low.
    // -------------------------------------------------------------------------
    logic                    ut_valid, ut_ready, ut_tvalid, ut_tready;
    logic [LANES-1:0][DW-1:0] ut_data;
    logic                    ut_gate;
    logic [15:0]             ut_underflow;
    int                      ut_errors, ut_dropped, ut_passed;

    axis_lane_debonder #(.LANES(LANES), .DATA_WIDTH(DW)) u_debonder_ut (
        .clk(clk), .rst_n(rst_n), .enable(1'b1),
        .s_lane_data(ut_data), .s_lane_valid(ut_valid),
        .s_lane_ready(ut_ready), .link_ok(ut_gate),
        .m_axis_tdata(), .m_axis_tvalid(ut_tvalid),
        .m_axis_tready(ut_tready), .underflow(ut_underflow)
    );

    task automatic ut_feed(input bit gate, input bit valid,
                           input logic [15:0] n);
        begin
            // Settle the gate one cycle before data so the drop counter is not
            // sampled on the same edge the gate changes.
            @(negedge clk);
            ut_gate   = gate;
            ut_tready = 1'b1;
            ut_valid  = 1'b0;
            @(negedge clk);
            for (int i = 0; i < n; i++) begin
                ut_valid = valid;
                ut_data  = {LANES{8'hA5}};
                @(posedge clk);
                #1;
                if (ut_tvalid && gate)  ut_passed  = ut_passed + 1;
                if (!ut_tvalid && valid) ut_dropped = ut_dropped + 1;
                @(negedge clk);
                ut_valid = 1'b0;
            end
        end
    endtask

    initial begin
        // ---- phase A: full loopback -------------------------------------
        repeat (4) @(posedge clk);
        rst_n  = 1'b1;
        enable = 1'b1;

        for (int t = 0; t < 20000 && rx_idx < NW; t++)
            @(posedge clk);
        repeat (40) @(posedge clk);

        if (rx_idx != NW) begin
            $display("ERROR: delivered %0d of %0d beats", rx_idx, NW);
            errors++;
        end
        if (!aligned) begin
            $display("ERROR: deskew not aligned at end"); errors++;
        end
        if (!link_ok) begin
            $display("ERROR: link_ok low at end"); errors++;
        end
        if (marker_errs != 0 || realign_count != 0) begin
            $display("ERROR: marker_error=%0d realign=%0d",
                     marker_errs, realign_count);
            errors++;
        end
        if (lane_fault !== '0) begin
            $display("ERROR: lane_fault=%b", lane_fault); errors++;
        end
        if (lane_locked !== {LANES{1'b1}}) begin
            $display("ERROR: lane_locked=%b", lane_locked); errors++;
        end
        if (rx_frame_locked !== {LANES{1'b1}}) begin
            $display("ERROR: rx_frame_locked=%b", rx_frame_locked); errors++;
        end
        if (underflow != 0) begin
            $display("ERROR: underflow=%0d on a clean link", underflow);
            errors++;
        end

        // Capture the loopback verdict before phase B disables the core, so
        // the summary line reports the link state that was actually checked.
        loopback_ok = link_ok;
        loopback_aligned = aligned;

        // ---- phase B: debonder gating unit test -------------------------
        enable   = 1'b0;          // free the loopback debonder
        ut_gate  = 1'b1;
        ut_errors = 0; ut_dropped = 0; ut_passed = 0;
        ut_feed(1'b1, 1'b1, 4);
        if (ut_passed != 4 || ut_dropped != 0) begin
            $display("ERROR: gate=1 passed=%0d dropped=%0d",
                     ut_passed, ut_dropped);
            errors++;
        end
        ut_feed(1'b0, 1'b1, 4);
        if (ut_dropped != 4) begin
            $display("ERROR: gate=0 dropped=%0d (expected 4)", ut_dropped);
            errors++;
        end
        if (ut_underflow != 4) begin
            $display("ERROR: underflow counter=%0d (expected 4)", ut_underflow);
            errors++;
        end
        if (ut_ready !== 1'b1) begin
            $display("ERROR: debonder stalls while gated"); errors++;
        end

        if (errors == 0)
            $display("PASS: tb_multilane_axis beats=%0d markers=%0d link_ok=%b underflow_ut=%0d",
                     rx_idx, marker_oks, loopback_ok, ut_underflow);
        else
            $display("FAIL: tb_multilane_axis errors=%0d beats=%0d aligned=%b link_ok=%b fault=%b",
                     errors, rx_idx, aligned, link_ok, lane_fault);
        $finish;
    end

    initial begin
        #2000000;
        $display("FAIL: tb_multilane_axis timeout beats=%0d errors=%0d",
                 rx_idx, errors);
        $finish;
    end

endmodule
