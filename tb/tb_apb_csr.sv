`timescale 1ns/1ps

// APB CSR smoke test: RW registers, read-only status, out-of-range error.
module tb_apb_csr;

    localparam int NUM_REGS = 8;
    localparam int DW       = 32;
    localparam int AW       = 16;

    logic clk;
    logic rst_n;
    logic [AW-1:0] paddr;
    logic psel;
    logic penable;
    logic pwrite;
    logic [DW-1:0] pwdata;
    logic pready;
    logic [DW-1:0] prdata;
    logic pslverr;
    logic [NUM_REGS-1:0][DW-1:0] regs_o;
    logic [DW-1:0] status_i;
    logic [NUM_REGS-1:0][DW-1:0] ro_i;

    int errors;

    always #5 clk = ~clk;

    apb_csr #(
        .NUM_REGS(NUM_REGS), .DATA_WIDTH(DW), .ADDR_WIDTH(AW),
        .STATUS_IDX(6), .RO_MASK(8'b0000_0100)
    ) dut (
        .pclk(clk), .presetn(rst_n),
        .paddr(paddr), .psel(psel), .penable(penable), .pwrite(pwrite),
        .pwdata(pwdata), .pready(pready), .prdata(prdata), .pslverr(pslverr),
        .regs_o(regs_o), .status_i(status_i), .ro_i(ro_i)
    );

    task automatic apb_write(input logic [AW-1:0] addr, input logic [DW-1:0] data);
        begin
            @(negedge clk);
            psel = 1'b1; penable = 1'b0; pwrite = 1'b1;
            paddr = addr; pwdata = data;
            @(posedge clk);
            @(negedge clk);
            penable = 1'b1;
            @(posedge clk);
            #1;
            if (!pready) begin
                $display("ERROR: pready low during write");
                errors++;
            end
            @(negedge clk);
            psel = 1'b0; penable = 1'b0; pwrite = 1'b0;
        end
    endtask

    task automatic apb_read(input logic [AW-1:0] addr, output logic [DW-1:0] data);
        begin
            @(negedge clk);
            psel = 1'b1; penable = 1'b0; pwrite = 1'b0;
            paddr = addr;
            @(posedge clk);
            @(negedge clk);
            penable = 1'b1;
            #1;
            data = prdata;
            if (!pready) begin
                $display("ERROR: pready low during read");
                errors++;
            end
            @(posedge clk);
            @(negedge clk);
            psel = 1'b0; penable = 1'b0;
        end
    endtask

    initial begin
        logic [DW-1:0] value;
        clk      = 1'b0;
        rst_n    = 1'b0;
        paddr    = '0;
        psel     = 1'b0;
        penable  = 1'b0;
        pwrite   = 1'b0;
        pwdata   = '0;
        status_i = 32'hA5A5_1234;
        ro_i     = '0;
        ro_i[2]  = 32'h5A5A_0F0F;
        errors   = 0;

        repeat (3) @(posedge clk);
        rst_n = 1'b1;
        repeat (2) @(posedge clk);

        apb_write(16'h0000, 32'h1234_5678);
        apb_write(16'h0004, 32'hDEAD_BEEF);
        apb_write(16'h0008, 32'hBAD0_0000); // read-only register: write ignored
        apb_write(16'h001C, 32'hC0DE_F00D); // last register is read/write here
        apb_read (16'h0000, value);
        if (value !== 32'h1234_5678) begin
            $display("ERROR: reg0 read %h", value);
            errors++;
        end
        apb_read (16'h0004, value);
        if (value !== 32'hDEAD_BEEF) begin
            $display("ERROR: reg1 read %h", value);
            errors++;
        end
        apb_read (16'h0008, value);
        if (value !== 32'h5A5A_0F0F) begin
            $display("ERROR: RO reg2 read %h", value);
            errors++;
        end
        apb_read (16'h0018, value);
        if (value !== status_i) begin
            $display("ERROR: status read %h", value);
            errors++;
        end
        apb_read (16'h001C, value);
        if (value !== 32'hC0DE_F00D) begin
            $display("ERROR: reg7 read %h", value);
            errors++;
        end
        apb_read (16'h0040, value);
        if (!pslverr) begin
            $display("ERROR: out-of-range read did not assert pslverr");
            errors++;
        end
        apb_write(16'h0008, 32'hFFFF_FFFF);
        if (regs_o[2] !== 32'h0) begin
            $display("ERROR: RO register was overwritten %h", regs_o[2]);
            errors++;
        end
        if (regs_o[0] !== 32'h1234_5678 || regs_o[1] !== 32'hDEAD_BEEF ||
            regs_o[7] !== 32'hC0DE_F00D) begin
            $display("ERROR: regs_o mismatch");
            errors++;
        end

        if (errors != 0) begin
            $display("FAIL: tb_apb_csr errors=%0d", errors);
            $fatal(1);
        end
        $display("PASS: tb_apb_csr");
        $finish;
    end

endmodule
