`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// Wishbone B4 slave -> APB bridge.
//
// The point of the bridge is that the APB register file stays the single source
// of truth: a Wishbone master must see the *same* CSR behaviour as an APB
// master, including the error paths.  So the test drives the real `apb_csr`
// through the bridge and checks:
//
//   1. a read of a read-only register returns its external value;
//   2. a write commits and reads back (read/write register);
//   3. writing a read-only register is ignored (no commit, no error);
//   4. an out-of-range address raises wb_err and does not commit;
//   5. a misaligned (non-word) address raises wb_err;
//   6. a partial wb_sel raises wb_err instead of silently writing the word;
//   7. back-to-back accesses work: one ack per access, no lost request.
// -----------------------------------------------------------------------------
module tb_wb_apb_bridge;

    localparam int AW = 16;
    localparam int DW = 32;
    localparam int NREGS = 8;

    logic clk = 0, rst_n = 0;
    always #5 clk = ~clk;

    int errors;

    // ---- Wishbone master ---------------------------------------------------
    logic          wb_cyc = 0, wb_stb = 0;
    logic [AW-1:0] wb_adr = '0;
    logic          wb_we = 0;
    logic [DW-1:0] wb_dat_w = '0;
    logic [DW/8-1:0] wb_sel = '1;
    logic          wb_ack, wb_err;
    logic [DW-1:0] wb_dat_r;

    // ---- APB to the real register file -------------------------------------
    logic          psel, penable;
    logic [AW-1:0] paddr;
    logic          pwrite;
    logic [DW-1:0] pwdata;
    logic          pready;
    logic [DW-1:0] prdata;
    logic          pslverr;

    logic [NREGS-1:0][DW-1:0] csr_regs;
    logic [DW-1:0]             status_i;
    logic [NREGS-1:0][DW-1:0]  ro_i;

    wb_apb_bridge #(.ADDR_WIDTH(AW), .DATA_WIDTH(DW)) u_wb2apb (
        .clk(clk), .rst_n(rst_n),
        .wb_cyc(wb_cyc), .wb_stb(wb_stb), .wb_adr(wb_adr), .wb_we(wb_we),
        .wb_dat_w(wb_dat_w), .wb_sel(wb_sel),
        .wb_ack(wb_ack), .wb_err(wb_err), .wb_dat_r(wb_dat_r),
        .psel(psel), .penable(penable), .paddr(paddr), .pwrite(pwrite),
        .pwdata(pwdata), .pready(pready), .prdata(prdata),
        .pslverr(pslverr)
    );

    /* verilator lint_off PINCONNECTEMPTY */
    apb_csr #(
        .NUM_REGS(NREGS), .DATA_WIDTH(DW), .ADDR_WIDTH(AW),
        .STATUS_IDX(7),
        // 6 and 7 read-only: 7 is the scalar status, 6 a second RO register.
        .RO_MASK(8'b1100_0000)
    ) u_csr (
        .pclk(clk), .presetn(rst_n),
        .paddr(paddr), .psel(psel), .penable(penable), .pwrite(pwrite),
        .pwdata(pwdata), .pready(pready), .prdata(prdata), .pslverr(pslverr),
        .regs_o(csr_regs), .status_i(status_i), .ro_i(ro_i)
    );
    /* verilator lint_on PINCONNECTEMPTY */

    // -------------------------------------------------------------------------
    // Wishbone master tasks: one access, one ack.
    // -------------------------------------------------------------------------
    task automatic wb_write(input logic [AW-1:0] adr, input logic [DW-1:0] dat,
                             input logic [DW/8-1:0] sel,
                             output bit err, output bit acked);
        begin
            @(negedge clk);
            wb_adr = adr; wb_dat_w = dat; wb_we = 1'b1; wb_sel = sel;
            wb_cyc = 1'b1; wb_stb = 1'b1;
            err = 0; acked = 0;
            for (int t = 0; t < 32 && !acked; t++) begin
                @(posedge clk);
                #1;
                if (wb_ack || wb_err) begin
                    acked = 1'b1;
                    err   = wb_err;
                end
            end
            @(negedge clk);
            wb_cyc = 1'b0; wb_stb = 1'b0;
        end
    endtask

    task automatic wb_read(input logic [AW-1:0] adr,
                            output logic [DW-1:0] dat,
                            output bit err, output bit acked);
        begin
            @(negedge clk);
            wb_adr = adr; wb_we = 1'b0; wb_sel = '1;
            wb_cyc = 1'b1; wb_stb = 1'b1;
            err = 0; acked = 0; dat = '0;
            for (int t = 0; t < 32 && !acked; t++) begin
                @(posedge clk);
                #1;
                if (wb_ack || wb_err) begin
                    acked = 1'b1;
                    err   = wb_err;
                    dat   = wb_dat_r;
                end
            end
            @(negedge clk);
            wb_cyc = 1'b0; wb_stb = 1'b0;
        end
    endtask

    initial begin
        errors = 0;
        status_i = 32'h1234_5678;
        ro_i = '0;
        ro_i[6] = 32'hCAFE_0001;
        ro_i[7] = '0;

        repeat (3) @(posedge clk);
        rst_n = 1'b1;
        repeat (2) @(posedge clk);

        // ---- 1. read the status register (STATUS_IDX=7) -------------------
        begin
            logic [DW-1:0] d; bit e, a;
            wb_read(AW'(7*4), d, e, a);
            if (!a || e) begin
                $display("ERROR: status read ack=%b err=%b", a, e); errors++;
            end
            if (d !== status_i) begin
                $display("ERROR: status read %08h expected %08h", d, status_i);
                errors++;
            end
        end

        // ---- 2. write / read back a read/write register -------------------
        begin
            logic [DW-1:0] d; bit e, a;
            wb_write(AW'(2*4), 32'hA5A5_0F0F, '1, e, a);
            if (!a || e) begin
                $display("ERROR: write ack=%b err=%b", a, e); errors++;
            end
            wb_read(AW'(2*4), d, e, a);
            if (!a || e || d !== 32'hA5A5_0F0F) begin
                $display("ERROR: readback %08h ack=%b err=%b", d, a, e);
                errors++;
            end
            // The APB file itself must hold the value: same source of truth.
            if (csr_regs[2] !== 32'hA5A5_0F0F) begin
                $display("ERROR: CSR2 %08h not committed through the bridge",
                         csr_regs[2]);
                errors++;
            end
        end

        // ---- 3. writing a read-only register is ignored -------------------
        begin
            logic [DW-1:0] d; bit e, a;
            wb_write(AW'(6*4), 32'hDEAD_BEEF, '1, e, a);
            if (!a || e) begin
                $display("ERROR: RO write ack=%b err=%b", a, e); errors++;
            end
            wb_read(AW'(6*4), d, e, a);
            if (d !== 32'hCAFE_0001) begin
                $display("ERROR: RO register changed to %08h", d); errors++;
            end
        end

        // ---- 4. out-of-range address raises wb_err ------------------------
        begin
            logic [DW-1:0] d; bit e, a;
            wb_read(AW'(64*4), d, e, a);
            if (!a || !e) begin
                $display("ERROR: out-of-range read ack=%b err=%b (expected err)",
                         a, e);
                errors++;
            end
        end

        // ---- 5. misaligned address raises wb_err --------------------------
        begin
            logic [DW-1:0] d; bit e, a;
            wb_read(AW'(2*4 + 1), d, e, a);
            if (!a || !e) begin
                $display("ERROR: misaligned read ack=%b err=%b", a, e);
                errors++;
            end
        end

        // ---- 6. partial wb_sel raises wb_err ------------------------------
        begin
            logic [DW-1:0] d; bit e, a;
            wb_write(AW'(3*4), 32'hFFFF_FFFF, 4'b0011, e, a);
            if (!a || !e) begin
                $display("ERROR: partial-sel write ack=%b err=%b", a, e);
                errors++;
            end
            if (csr_regs[3] !== 32'h0) begin
                $display("ERROR: partial-sel write committed %08h", csr_regs[3]);
                errors++;
            end
        end

        // ---- 7. back-to-back accesses -------------------------------------
        begin
            logic [DW-1:0] d; bit e, a;
            for (int i = 0; i < 8; i++) begin
                wb_write(AW'(4*4), 32'(i * 3), '1, e, a);
                if (!a || e) begin
                    $display("ERROR: burst write %0d ack=%b err=%b", i, a, e);
                    errors++;
                end
                wb_read(AW'(4*4), d, e, a);
                if (!a || e || d !== 32'(i * 3)) begin
                    $display("ERROR: burst read %0d got %0d ack=%b err=%b",
                             i, d, a, e);
                    errors++;
                end
            end
        end

        if (errors == 0)
            $display("PASS: tb_wb_apb_bridge read/write/RO/range/align/sel/burst ok");
        else
            $display("FAIL: tb_wb_apb_bridge errors=%0d", errors);
        $finish;
    end

    initial begin
        #500000;
        $display("FAIL: tb_wb_apb_bridge timeout errors=%0d", errors);
        $finish;
    end

endmodule
