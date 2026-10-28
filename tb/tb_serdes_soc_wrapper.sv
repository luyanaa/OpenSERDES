`timescale 1ns/1ps

module tb_serdes_soc_wrapper;

    localparam int DW    = 8;
    localparam int WORDS = 4;
    localparam int RATIO = DW * WORDS;

    logic clk;
    logic rst_n;
    logic [15:0] paddr;
    logic psel;
    logic penable;
    logic pwrite;
    logic [31:0] pwdata;
    logic pready;
    logic [31:0] prdata;
    logic pslverr;

    logic s_axis_tvalid;
    logic s_axis_tready;
    logic [DW-1:0] s_axis_tdata;
    logic s_axis_tlast;
    logic m_axis_tvalid;
    logic m_axis_tready;
    logic [DW-1:0] m_axis_tdata;
    logic m_axis_tlast;

    logic tx_serial_out;
    logic tx_serial_valid;
    logic rx_serial_in;
    logic rx_sample_strobe;
    logic rx_serial_data;
    logic rx_serial_valid;
    logic signed [12:0] rx_freq_trim;
    logic rx_clk_out;
    logic [7:0][31:0] csr_regs;

    int errors;
    logic [RATIO-1:0] payload;

    always #5 clk = ~clk;

    assign rx_serial_in = tx_serial_out;

    serdes_soc_wrapper #(
        .DATA_WIDTH(DW), .WORDS(WORDS), .LSB_FIRST(1'b1),
        .SYNC_LEN(8), .SYNC_PATTERN(8'hBC)
    ) dut (
        .clk(clk), .rst_n(rst_n),
        .paddr(paddr), .psel(psel), .penable(penable), .pwrite(pwrite),
        .pwdata(pwdata), .pready(pready), .prdata(prdata), .pslverr(pslverr),
        .s_axis_tvalid(s_axis_tvalid), .s_axis_tready(s_axis_tready),
        .s_axis_tdata(s_axis_tdata), .s_axis_tlast(s_axis_tlast),
        .m_axis_tvalid(m_axis_tvalid), .m_axis_tready(m_axis_tready),
        .m_axis_tdata(m_axis_tdata), .m_axis_tlast(m_axis_tlast),
        .tx_serial_out(tx_serial_out), .tx_serial_valid(tx_serial_valid),
        .rx_serial_in(rx_serial_in), .rx_sample_strobe(rx_sample_strobe),
        .rx_serial_data(rx_serial_data), .rx_serial_valid(rx_serial_valid),
        .rx_freq_trim(rx_freq_trim), .rx_clk_out(rx_clk_out),
        .csr_regs(csr_regs)
    );

    task automatic apb_write(input logic [15:0] addr, input logic [31:0] data);
        begin
            @(negedge clk);
            psel = 1'b1; penable = 1'b0; pwrite = 1'b1;
            paddr = addr; pwdata = data;
            @(posedge clk);
            @(negedge clk);
            penable = 1'b1;
            @(posedge clk);
            #1;
            @(negedge clk);
            psel = 1'b0; penable = 1'b0; pwrite = 1'b0;
        end
    endtask

    task automatic apb_read(input logic [15:0] addr, output logic [31:0] data);
        begin
            @(negedge clk);
            psel = 1'b1; penable = 1'b0; pwrite = 1'b0;
            paddr = addr;
            @(posedge clk);
            @(negedge clk);
            penable = 1'b1;
            #1;
            data = prdata;
            @(posedge clk);
            @(negedge clk);
            psel = 1'b0; penable = 1'b0;
        end
    endtask

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
        logic [31:0] status;
        logic [DW-1:0] data;
        logic last;
        clk           = 1'b0;
        rst_n         = 1'b0;
        paddr         = '0;
        psel          = 1'b0;
        penable       = 1'b0;
        pwrite        = 1'b0;
        pwdata        = '0;
        s_axis_tvalid = 1'b0;
        s_axis_tdata  = '0;
        s_axis_tlast  = 1'b0;
        m_axis_tready = 1'b0;
        errors        = 0;
        payload       = 32'h0123_4567;

        repeat (3) @(posedge clk);
        rst_n = 1'b1;
        repeat (3) @(posedge clk);

        apb_write(16'h0000, 32'h0000_0003); // tx_enable | rx_enable
        apb_write(16'h0004, 32'hCAFE_BABE); // scratch
        apb_read (16'h0004, status);
        if (status !== 32'hCAFE_BABE) begin
            $display("ERROR: APB scratch read %h", status);
            errors++;
        end

        // TX frame: comma + 4 payload words.
        send_word(8'hBC, 1'b0);
        for (int w = 0; w < WORDS; w++)
            send_word(payload[w*DW +: DW], (w == WORDS-1));

        // RX frame: 4 payload words.
        for (int w = 0; w < WORDS; w++) begin
            read_beat(data, last);
            if (data !== payload[w*DW +: DW]) begin
                $display("ERROR: RX AXIS word %0d got %h expected %h",
                         w, data, payload[w*DW +: DW]);
                errors++;
            end
            if (last !== (w == WORDS-1)) begin
                $display("ERROR: RX AXIS tlast wrong at word %0d", w);
                errors++;
            end
        end

        apb_read(16'h001C, status);
        if (!status[4]) begin
            $display("ERROR: status rx_lock low");
            errors++;
        end
        if (!status[6]) begin
            $display("ERROR: status rx_frame_locked low");
            errors++;
        end
        if (status[7]) begin
            $display("ERROR: status rx_frame_overflow high");
            errors++;
        end
        if (status[3]) begin
            $display("ERROR: status tx_frame_error high");
            errors++;
        end

        if (errors != 0) begin
            $display("FAIL: tb_serdes_soc_wrapper errors=%0d", errors);
            $fatal(1);
        end
        $display("PASS: tb_serdes_soc_wrapper");
        $finish;
    end

endmodule
