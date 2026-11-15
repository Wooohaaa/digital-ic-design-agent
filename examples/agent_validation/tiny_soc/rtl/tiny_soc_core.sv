`timescale 1ns/1ps
// tiny_soc_core.sv - RSI roadmap stage 7: SoC fabric (no CPU).
//
// This is the logic the stage-7 integration fixture actually contributes:
// interconnect/window decode, control & status registers, operand/result SRAM,
// an integer GEMM accelerator, an 8N1 UART transmitter, and an instruction
// memory. It is parameterised over the bus geometry so the CPU-side bus mux
// lives in the tiny_soc wrapper, where it can be replaced by any master.
//
// Bus: an AXI-like handshaked write (bus_we + bus_wready: the master waits
// for ready) and a combinational read (bus_rdata = bus_addr, bus_rvalid).
// Addresses are 9-bit byte addresses, one 32-bit word per address.
//
//   0x00 CTRL      [0] start GEMM (write pulse)
//   0x04 STATUS    [0] GEMM done (read-only)
//   0x08 RESULT    writable register (the CPU stores C[0] here)
//   0x0C TXDATA    write: transmit the low byte over UART
//   0x10 TXSTATUS  [0] UART busy (read-only)
//   0x20 DMA_SRC [1:0] / 0x24 DMA_DST [1:0] / 0x28 DMA_SRCS [2:0]
//   0x2C DMA_DSTS [2:0] / 0x30 DMA_CTRL [0] start, [1] chain / 0x34 DMA_STATUS
//   0x38 DMA_HEAD  [1:0] first descriptor index (chain mode)
//   0x40 + 4*i     A word i (row-major)
//   0x80 + 4*i     B word i (row-major)
//   0xC0 + 4*i     C word i (row-major, read-back)
//   0x100 + 4*i    DMA descriptor i (13-bit fields: src/dst windows, row
//                  strides, last flag, next pointer) - programmed by the
//                  host, walked by the DMA in chain mode
//
// The instruction memory is word-indexed (8-bit index) so the fabric does not
// expose a 32-bit byte address for the CPU; that keeps every fabric address
// bit toggle-coverable from a program, and avoids the RV32I word-aligned PC
// making fabric address bits structurally unreachable.

module tiny_soc_core #(
    parameter int unsigned N        = 4,
    parameter int unsigned DW       = 32,
    parameter int unsigned BAUD_DIV = 8
) (
    input  logic          clk,
    input  logic          rst_n,
    // ---- host bus (interconnect) ----
    input  logic          bus_we,
    input  logic [3:0]    bus_wstrb,
    input  logic [8:0]    bus_addr,
    input  logic [DW-1:0] bus_wdata,
    output logic [DW-1:0] bus_rdata,
    // AXI-like handshaking: bus_wready signals when the operand-SRAM write
    // port is available (only when the GEMM is idle); bus_rvalid is
    // constant 1 because reads are combinational. Register writes
    // (CTRL/RESULT/TXDATA/DMA/descriptor) do not touch the operand SRAM and
    // are accepted regardless of ready.
    output logic          bus_wready,
    output logic          bus_rvalid,
    // ---- instruction memory load (word-indexed) ----
    input  logic          imem_ld_we,
    input  logic [7:0]    imem_ld_widx,
    input  logic [DW-1:0] imem_ld_wdata,
    // ---- instruction memory read (word-indexed, via the I-cache) ----
    input  logic [7:0]    imem_idx,
    output logic [DW-1:0] imem_rd,
    // I-cache stall: high while the cache refills a line, so the wrapper
    // gates the CPU clock (the reused RV32I core has no stall input).
    output logic          imem_stall,
    // ---- serial I/O ----
    output logic          uart_tx,
    // ---- DMA interrupt (asserted on copy done, cleared on next start) ----
    output logic          dma_irq
);

    localparam int unsigned IW = (N > 1) ? $clog2(N) : 1;
    localparam int unsigned AW = (N*N > 1) ? $clog2(N*N) : 1;
    localparam int unsigned CW = (BAUD_DIV > 1) ? $clog2(BAUD_DIV) : 1;
    localparam logic [CW-1:0] BAUD_LAST = CW'(BAUD_DIV - 1);

    // ---- on-chip SRAM ----------------------------------------------------
    logic [DW-1:0] amem [0:N*N-1];
    logic [DW-1:0] bmem [0:N*N-1];
    logic [DW-1:0] cmem [0:N*N-1];
    logic [DW-1:0] imem [0:255];

    // ---- instruction memory ---------------------------------------------
    always_ff @(posedge clk) begin
        if (imem_ld_we) imem[imem_ld_widx] <= imem_ld_wdata;
    end

    // ---- instruction cache: direct-mapped, 4 lines x 4 words ---------------
    // The RV32I core fetches combinationally in the same cycle and has no
    // stall input, so the cache is *blocking*: on a miss the fabric asserts
    // imem_stall, the wrapper gates the CPU clock, and the fabric refills the
    // 4-word line from IMEM one word per cycle before releasing the CPU. A
    // NOP (addi x0,x0,0 = 0x00000013) is served while refilling so the
    // clock-gated CPU cannot decode a spurious memory access. The cache is
    // exercised transparently by the CPU fetch path and directly through the
    // probe window (bus address 0x014 control, 0x018 data).
    //
    //   line  = imem_idx[3:2]   (4 lines)
    //   offset= imem_idx[1:0]   (4 words per line)
    //   tag   = imem_idx[7:4]   (16-word block, so lines conflict)
    localparam int unsigned IC_LINES = 4;
    localparam int unsigned IC_W     = 4;

    logic [DW-1:0] ic_line  [0:IC_LINES*IC_W-1];
    logic          ic_valid [0:IC_LINES-1];
    logic [3:0]    ic_tag   [0:IC_LINES-1];

    logic          ic_probe_en;
    logic [7:0]    ic_probe_idx;
    logic          ic_fill_busy;
    logic [1:0]    ic_fill_off;
    logic [1:0]    ic_fill_idx;
    logic [3:0]    ic_fill_tag;
    integer        ic_k;

    wire [7:0]    fetch_idx  = ic_probe_en ? ic_probe_idx : imem_idx;
    wire [1:0]    ic_lidx    = fetch_idx[3:2];
    wire [1:0]    ic_off     = fetch_idx[1:0];
    wire [3:0]    ic_tag_v   = fetch_idx[7:4];
    wire          ic_hit     = ic_valid[ic_lidx] && (ic_tag[ic_lidx] == ic_tag_v);
    wire          ic_miss    = !ic_hit;
    wire [DW-1:0] ic_word    = ic_line[{ic_lidx, ic_off}];

    assign imem_rd    = ic_fill_busy ? DW'(32'h00000013)
                                    : (ic_hit ? ic_word : imem[fetch_idx]);
    assign imem_stall = ic_fill_busy;

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            ic_probe_en  <= 1'b0;
            ic_probe_idx <= 8'd0;
            ic_fill_busy <= 1'b0;
            ic_fill_off  <= 2'd0;
            ic_fill_idx  <= 2'd0;
            ic_fill_tag  <= 4'd0;
            for (ic_k = 0; ic_k < IC_LINES; ic_k = ic_k + 1) begin
                ic_valid[ic_k] <= 1'b0;
                ic_tag[ic_k]   <= 4'd0;
            end
        end else begin
            // probe/control window write (bus address 0x014):
            //   bit 9  = flush (clear all valid bits)
            //   bit 8  = probe enable
            //   bits 7:0 = probe word index
            if (bus_we && (bus_addr == 9'h014)) begin
                ic_probe_en  <= bus_wdata[8];
                ic_probe_idx <= bus_wdata[7:0];
                if (bus_wdata[9]) begin
                    for (ic_k = 0; ic_k < IC_LINES; ic_k = ic_k + 1)
                        ic_valid[ic_k] <= 1'b0;
                end
            end
            // refill progression (one word per cycle)
            if (ic_fill_busy) begin
                ic_line[{ic_fill_idx, ic_fill_off}] <= imem[{ic_fill_tag, ic_fill_idx, ic_fill_off}];
                if (ic_fill_off == 2'd3) begin
                    ic_valid[ic_fill_idx] <= 1'b1;
                    ic_tag[ic_fill_idx]   <= ic_fill_tag;
                    ic_fill_busy          <= 1'b0;
                end else begin
                    ic_fill_off <= ic_fill_off + 1'b1;
                end
            end else if (bus_we && (bus_addr == 9'h014) && bus_wdata[8]) begin
                // probe-triggered refill (a flush always forces one)
                if (bus_wdata[9] || !ic_valid[bus_wdata[3:2]] ||
                    (ic_tag[bus_wdata[3:2]] != bus_wdata[7:4])) begin
                    ic_fill_busy <= 1'b1;
                    ic_fill_off  <= 2'd0;
                    ic_fill_idx  <= bus_wdata[3:2];
                    ic_fill_tag  <= bus_wdata[7:4];
                end
            end else if (ic_miss && !ic_probe_en) begin
                // CPU-fetch refill (only while the CPU is running)
                ic_fill_busy <= 1'b1;
                ic_fill_off  <= 2'd0;
                ic_fill_idx  <= ic_lidx;
                ic_fill_tag  <= ic_tag_v;
            end
        end
    end

    // ---- interconnect: window decode ------------------------------------
    wire          sel_a  = (bus_addr >= 9'h40) && (bus_addr < 9'h80);
    wire          sel_b  = (bus_addr >= 9'h80) && (bus_addr < 9'hC0);
    wire          sel_c  = (bus_addr >= 9'hC0) && (bus_addr < 9'h100);
    wire          sel_dma = (bus_addr >= 9'h20) && (bus_addr < 9'h40);
    wire          sel_desc = (bus_addr >= 9'h100) && (bus_addr < 9'h110);
    wire [AW-1:0] mem_idx = bus_addr[5:2];
    wire [4:0]    dma_s_addr = sel_dma ? bus_addr[4:0] : 5'h0;

    // ---- DMA master write decode ----------------------------------------
    wire         dma_m_we;
    wire [7:0]   dma_m_waddr;
    wire [DW-1:0] dma_m_wdata;
    wire [7:0]   dma_m_raddr;
    wire [DW-1:0] dma_m_rdata;
    wire dma_sel_a = (dma_m_waddr >= 8'h40) && (dma_m_waddr < 8'h80);
    wire dma_sel_b = (dma_m_waddr >= 8'h80) && (dma_m_waddr < 8'hC0);
    wire dma_sel_c = (dma_m_waddr >= 8'hC0);
    wire [AW-1:0] dma_mem_idx = dma_m_waddr[5:2];

    // ---- DMA master read decode ------------------------------------------
    wire dma_rd_sel_a = (dma_m_raddr >= 8'h40) && (dma_m_raddr < 8'h80);
    wire dma_rd_sel_b = (dma_m_raddr >= 8'h80) && (dma_m_raddr < 8'hC0);
    wire dma_rd_sel_c = (dma_m_raddr >= 8'hC0);
    wire [AW-1:0] dma_rd_idx = dma_m_raddr[5:2];

    // ---- control/status ---------------------------------------------------
    logic          ctrl_start;
    logic          gemm_done;
    logic          running;
    logic          tx_busy;
    logic [DW-1:0] result_reg;

    // AXI-like handshaking: bus_wready declares the operand-SRAM write port
    // available (only when the GEMM is idle - writing A/B mid-run would
    // corrupt the computation). Register writes (CTRL/RESULT/TXDATA/DMA/
    // descriptor) do not touch the operand SRAM, so they are accepted
    // regardless of ready. Reads are always available (combinational), so
    // bus_rvalid is constant 1.
    assign bus_wready = !running;
    assign bus_rvalid = 1'b1;

    // ---- control register (start is a pulse) ----
    always_ff @(posedge clk) begin
        if (!rst_n) begin
            ctrl_start <= 1'b0;
        end else begin
            ctrl_start <= 1'b0;
            if (bus_we && (bus_addr == 9'h000)) ctrl_start <= bus_wdata[0];
        end
    end

    // ---- RESULT register (writable by the bus) ----
    always_ff @(posedge clk) begin
        if (!rst_n) result_reg <= {DW{1'b0}};
        else if (bus_we && (bus_addr == 9'h008)) result_reg <= bus_wdata;
    end

    // ---- operand SRAM (the AXI-backpressured resource: writable only while
    //      the GEMM is idle, as signalled by bus_wready) ----
    // DMA master has priority over the host bus while the GEMM is idle.
    integer lane;
    always_ff @(posedge clk) begin
        if (dma_m_we && !running) begin
            if (dma_sel_a) amem[dma_mem_idx] <= dma_m_wdata;
            else if (dma_sel_b) bmem[dma_mem_idx] <= dma_m_wdata;
        end else if (bus_we && bus_wready) begin
            if (sel_a) begin
                for (lane = 0; lane < 4; lane = lane + 1)
                    if (bus_wstrb[lane]) amem[mem_idx][8*lane +: 8] <= bus_wdata[8*lane +: 8];
            end else if (sel_b) begin
                for (lane = 0; lane < 4; lane = lane + 1)
                    if (bus_wstrb[lane]) bmem[mem_idx][8*lane +: 8] <= bus_wdata[8*lane +: 8];
            end
        end
    end

    // ---- data cache: direct-mapped, 4 lines x 4 words ----------------------
    // The RV32I core reads dmem combinationally and has no stall input, so the
    // D-cache is *non-blocking*: a read miss bypasses to the SRAM in the same
    // cycle (so the CPU always sees correct data) while the 4-word line is
    // refilled in a single cycle. Coherence is by invalidation (no write-back):
    //   * any host or DMA write that lands in an A/B/C word clears that line
    //   * the GEMM clears the whole cache when it starts, because it rewrites
    //     C in place and would otherwise leave cached C lines stale
    //   * a probe/control window (0x01C control, 0x01D data) can flush it and
    //     makes hit/miss observable
    // The cache is therefore transparent: every read (CPU or host) returns
    // coherent data, hit or miss.
    //
    //   line  = addr[5:4]   (4 lines)
    //   offset= addr[3:2]   (4 words per line)
    //   tag   = addr[7:6]   (1=A, 2=B, 3=C; 0=uncached registers/DMA)
    localparam int unsigned DC_W = 4;

    logic [DW-1:0] dc_line  [0:IC_LINES*DC_W-1];
    logic          dc_valid [0:IC_LINES-1];
    logic [1:0]    dc_tag   [0:IC_LINES-1];

    logic          dc_probe_en;
    logic [5:0]    dc_probe_addr;
    integer        dc_k;

    wire [5:0]    dc_addr    = dc_probe_en ? dc_probe_addr : bus_addr[7:2];
    wire [1:0]    dc_lidx    = dc_addr[3:2];
    wire [1:0]    dc_off     = dc_addr[1:0];
    wire [1:0]    dc_tag_v   = dc_addr[5:4];
    wire          dc_cacheable = (dc_tag_v != 2'b00) &&
                                 (dc_probe_en || sel_a || sel_b || sel_c);
    wire          dc_hit     = dc_cacheable && dc_valid[dc_lidx] &&
                               (dc_tag[dc_lidx] == dc_tag_v);
    wire          dc_refill  = dc_cacheable && !dc_hit && !bus_we && !dc_probe_en;
    wire          dc_rd_hit  = dc_hit && !dc_probe_en && (sel_a || sel_b || sel_c);
    wire [DW-1:0] dc_word    = dc_line[{dc_lidx, dc_off}];

    // word index within the 16-word window = {line, offset}
    wire [DW-1:0] dc_w0 = (dc_tag_v == 2'd1) ? amem[{dc_lidx, 2'd0}] : (dc_tag_v == 2'd2) ? bmem[{dc_lidx, 2'd0}] : cmem[{dc_lidx, 2'd0}];
    wire [DW-1:0] dc_w1 = (dc_tag_v == 2'd1) ? amem[{dc_lidx, 2'd1}] : (dc_tag_v == 2'd2) ? bmem[{dc_lidx, 2'd1}] : cmem[{dc_lidx, 2'd1}];
    wire [DW-1:0] dc_w2 = (dc_tag_v == 2'd1) ? amem[{dc_lidx, 2'd2}] : (dc_tag_v == 2'd2) ? bmem[{dc_lidx, 2'd2}] : cmem[{dc_lidx, 2'd2}];
    wire [DW-1:0] dc_w3 = (dc_tag_v == 2'd1) ? amem[{dc_lidx, 2'd3}] : (dc_tag_v == 2'd2) ? bmem[{dc_lidx, 2'd3}] : cmem[{dc_lidx, 2'd3}];
    wire [DW-1:0] dc_bypass = (dc_tag_v == 2'd1) ? amem[{dc_lidx, dc_off}]
                            : (dc_tag_v == 2'd2) ? bmem[{dc_lidx, dc_off}]
                            : cmem[{dc_lidx, dc_off}];
    wire [DW-1:0] dc_probe_word = dc_hit ? dc_word : dc_bypass;

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            dc_probe_en   <= 1'b0;
            dc_probe_addr <= 6'd0;
            for (dc_k = 0; dc_k < IC_LINES; dc_k = dc_k + 1) begin
                dc_valid[dc_k] <= 1'b0;
                dc_tag[dc_k]   <= 2'd0;
            end
        end else begin
            // probe/control window write (0x01C):
            //   bit 10 = fill (refill the probe line from SRAM)
            //   bit  9 = flush (clear all valid bits)
            //   bit  8 = probe enable
            //   bits 7:0 = probe address (an A/B/C word address)
            if (bus_we && (bus_addr == 9'h01C)) begin
                dc_probe_en   <= bus_wdata[8];
                dc_probe_addr <= bus_wdata[7:2];
                if (bus_wdata[9]) begin
                    for (dc_k = 0; dc_k < IC_LINES; dc_k = dc_k + 1)
                        dc_valid[dc_k] <= 1'b0;
                end
                if (bus_wdata[10]) begin
                    dc_line[{bus_wdata[5:4], 2'd0}] <=
                        (bus_wdata[7:6] == 2'd1) ? amem[{bus_wdata[5:4], 2'd0}]
                        : (bus_wdata[7:6] == 2'd2) ? bmem[{bus_wdata[5:4], 2'd0}]
                        : cmem[{bus_wdata[5:4], 2'd0}];
                    dc_line[{bus_wdata[5:4], 2'd1}] <=
                        (bus_wdata[7:6] == 2'd1) ? amem[{bus_wdata[5:4], 2'd1}]
                        : (bus_wdata[7:6] == 2'd2) ? bmem[{bus_wdata[5:4], 2'd1}]
                        : cmem[{bus_wdata[5:4], 2'd1}];
                    dc_line[{bus_wdata[5:4], 2'd2}] <=
                        (bus_wdata[7:6] == 2'd1) ? amem[{bus_wdata[5:4], 2'd2}]
                        : (bus_wdata[7:6] == 2'd2) ? bmem[{bus_wdata[5:4], 2'd2}]
                        : cmem[{bus_wdata[5:4], 2'd2}];
                    dc_line[{bus_wdata[5:4], 2'd3}] <=
                        (bus_wdata[7:6] == 2'd1) ? amem[{bus_wdata[5:4], 2'd3}]
                        : (bus_wdata[7:6] == 2'd2) ? bmem[{bus_wdata[5:4], 2'd3}]
                        : cmem[{bus_wdata[5:4], 2'd3}];
                    dc_valid[bus_wdata[5:4]] <= 1'b1;
                    dc_tag[bus_wdata[5:4]]   <= bus_wdata[7:6];
                end
            end
            // refill the line on a read miss (whole line in one cycle)
            if (dc_refill) begin
                dc_line[{dc_lidx, 2'd0}] <= dc_w0;
                dc_line[{dc_lidx, 2'd1}] <= dc_w1;
                dc_line[{dc_lidx, 2'd2}] <= dc_w2;
                dc_line[{dc_lidx, 2'd3}] <= dc_w3;
                dc_valid[dc_lidx] <= 1'b1;
                dc_tag[dc_lidx]   <= dc_tag_v;
            end
            // the GEMM rewrites C in place: clear the cache when it starts
            if (ctrl_start && !running) begin
                for (dc_k = 0; dc_k < IC_LINES; dc_k = dc_k + 1)
                    dc_valid[dc_k] <= 1'b0;
            end
            // write-through coherence: a host/DMA write clears the line it hits
            // (clears are ordered after the refill so they always win)
            if (bus_we && bus_wready && (sel_a || sel_b || sel_c))
                dc_valid[bus_addr[5:4]] <= 1'b0;
            if (dma_m_we && !running)
                dc_valid[dma_m_waddr[5:4]] <= 1'b0;
        end
    end

    // ---- GEMM datapath: triple-nested lockstep loop ----
    logic [IW-1:0] idx_i, idx_j, idx_k;
    logic [DW-1:0] acc;

    wire [AW-1:0] a_idx   = AW'(idx_i) * AW'(N) + AW'(idx_k);
    wire [AW-1:0] b_idx   = AW'(idx_k) * AW'(N) + AW'(idx_j);
    wire [AW-1:0] c_idx   = AW'(idx_i) * AW'(N) + AW'(idx_j);
    wire [DW-1:0] a_val   = amem[a_idx];
    wire [DW-1:0] b_val   = bmem[b_idx];
    wire [DW-1:0] product = a_val * b_val;
    wire [DW-1:0] new_acc = acc + product;

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            idx_i     <= {IW{1'b0}};
            idx_j     <= {IW{1'b0}};
            idx_k     <= {IW{1'b0}};
            acc       <= {DW{1'b0}};
            running   <= 1'b0;
            gemm_done <= 1'b0;
        end else if (ctrl_start && !running) begin
            idx_i     <= {IW{1'b0}};
            idx_j     <= {IW{1'b0}};
            idx_k     <= {IW{1'b0}};
            acc       <= {DW{1'b0}};
            running   <= 1'b1;
            gemm_done <= 1'b0;
        end else if (running) begin
            if (idx_k == IW'(N-1)) begin
                idx_k <= {IW{1'b0}};
                acc   <= {DW{1'b0}};
                if (idx_j == IW'(N-1)) begin
                    if (idx_i == IW'(N-1)) begin
                        running   <= 1'b0;
                        gemm_done <= 1'b1;
                    end else begin
                        idx_i <= idx_i + 1'b1;
                        idx_j <= {IW{1'b0}};
                    end
                end else begin
                    idx_j <= idx_j + 1'b1;
                end
            end else begin
                acc   <= new_acc;
                idx_k <= idx_k + 1'b1;
            end
        end
    end

    // ---- C memory: cleared on reset, written by the loop ----
    integer ci;
    always_ff @(posedge clk) begin
        if (!rst_n) begin
            for (ci = 0; ci < N*N; ci = ci + 1) cmem[ci] <= {DW{1'b0}};
        end else if (running && (idx_k == IW'(N-1))) begin
            // Store ReLU(new_acc): a sum whose sign bit is set clamps to 0.
            cmem[c_idx] <= new_acc[DW-1] ? {DW{1'b0}} : new_acc;
        end else if (dma_m_we && !running && dma_sel_c) begin
            // DMA master writes C while the GEMM is idle.
            cmem[dma_mem_idx] <= dma_m_wdata;
        end else if (bus_we && bus_wready && sel_c) begin
            // Host bus writes C while the GEMM is idle (same byte-lane
            // strobes as the A/B operand SRAM).
            for (lane = 0; lane < 4; lane = lane + 1)
                if (bus_wstrb[lane]) cmem[mem_idx][8*lane +: 8] <= bus_wdata[8*lane +: 8];
        end
    end

    // ---- DMA engine (second bus master): 2D scatter/gather + descriptor
    //      chains + interrupt ---------------------------------------------
    logic [2:0]  dma_s_rdata;
    logic [1:0]  desc_raddr;
    logic [12:0] desc_rdata;
    tiny_dma #(.N(N), .DW(DW)) u_dma (
        .clk        (clk),
        .rst_n      (rst_n),
        .s_we       (bus_we && sel_dma),
        .s_addr     (dma_s_addr),
        .s_wdata    (bus_wdata[2:0]),
        .s_rdata    (dma_s_rdata),
        .m_we       (dma_m_we),
        .m_waddr    (dma_m_waddr),
        .m_wdata    (dma_m_wdata),
        .m_raddr    (dma_m_raddr),
        .m_rdata    (dma_m_rdata),
        .dma_irq    (dma_irq),
        .desc_raddr (desc_raddr),
        .desc_rdata (desc_rdata)
    );

    // ---- DMA descriptor table (host-programmed, DMA-walked) -------------
    // 4 slots x 13 bits. The host writes a slot through the descriptor
    // window; the DMA reads the slot its desc_idx points at via the
    // dedicated descriptor read port.
    logic [12:0] desc_mem [0:3];
    always_ff @(posedge clk) begin
        if (bus_we && sel_desc) desc_mem[bus_addr[3:2]] <= bus_wdata[12:0];
    end
    assign desc_rdata = desc_mem[desc_raddr];

    // DMA master read path (separate from the bus read so the CPU can poll
    // DMA_STATUS while a copy is running).
    logic [DW-1:0] dma_rd;
    always_comb begin
        dma_rd = {DW{1'b0}};
        if      (dma_rd_sel_a) dma_rd = amem[dma_rd_idx];
        else if (dma_rd_sel_b) dma_rd = bmem[dma_rd_idx];
        else if (dma_rd_sel_c) dma_rd = cmem[dma_rd_idx];
    end
    assign dma_m_rdata = dma_rd;

    // ---- UART transmitter: 8N1, LSB first ----
    // tx_bit: 0 = start, 1..8 = data bit (tx_bit-1), 9 = stop
    logic [3:0]    tx_bit;
    logic [CW-1:0] tx_cnt;
    logic [7:0]    tx_data_r;
    wire           tx_start = bus_we && (bus_addr == 9'h00C) && !tx_busy;
    wire [2:0]     tx_data_sel = tx_bit[2:0] - 3'd1;
    wire           tx_level = (tx_bit == 4'd0) ? 1'b0
                           : (tx_bit == 4'd9) ? 1'b1
                           : tx_data_r[tx_data_sel];

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            tx_busy   <= 1'b0;
            tx_bit    <= 4'd0;
            tx_cnt    <= {CW{1'b0}};
            tx_data_r <= 8'h00;
        end else if (!tx_busy) begin
            tx_cnt <= {CW{1'b0}};
            tx_bit <= 4'd0;
            if (tx_start) begin
                tx_busy   <= 1'b1;
                tx_data_r <= bus_wdata[7:0];
            end
        end else begin
            if (tx_cnt >= BAUD_LAST) begin
                tx_cnt <= {CW{1'b0}};
                if (tx_bit == 4'd9) tx_busy <= 1'b0;
                else                tx_bit <= tx_bit + 1'b1;
            end else begin
                tx_cnt <= tx_cnt + 1'b1;
            end
        end
    end

    assign uart_tx = tx_busy ? tx_level : 1'b1;

    // ---- read-back path (combinational) ----
    logic [DW-1:0] rd;
    always_comb begin
        rd = {DW{1'b0}};
        if      (dc_rd_hit) rd = dc_word;
        else if (sel_a) rd = amem[mem_idx];
        else if (sel_b) rd = bmem[mem_idx];
        else if (sel_c) rd = cmem[mem_idx];
        else if (sel_dma) rd = {{DW-3{1'b0}}, dma_s_rdata};
        else if (sel_desc) rd = {19'b0, desc_mem[bus_addr[3:2]]};
        else case (bus_addr)
            9'h004:  rd = DW'(gemm_done);
            9'h008:  rd = result_reg;
            9'h010:  rd = DW'(tx_busy);
            9'h014:  rd = {{DW-2{1'b0}}, ic_fill_busy, ic_hit};  // IC status
            9'h018:  rd = ic_hit ? ic_word : imem[fetch_idx];     // IC data
            9'h01C:  rd = {{DW-2{1'b0}}, dc_valid[dc_lidx], dc_hit};  // DC status
            9'h01D:  rd = dc_probe_word;                              // DC data
            default: rd = {DW{1'b0}};
        endcase
    end
    assign bus_rdata = rd;

endmodule
