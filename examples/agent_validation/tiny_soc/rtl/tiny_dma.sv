`timescale 1ns/1ps
// tiny_dma.sv - RSI roadmap stage 7: DMA engine (second bus master).
//
// Copies N*N words from a source SRAM window to a destination SRAM window,
// one word per clock, ROW BY ROW with configurable row strides. The row stride
// (word-indexed) lets the same engine do a linear memcpy (stride = N), a
// gather (source stride != N) and a scatter (destination stride != N).
//
// On top of that it supports DESCRIPTOR CHAINS: the CPU programs a small
// table of one-word descriptors in the fabric (bus window 0x100..0x10F) and
// sets DMA_HEAD + the chain bit in DMA_CTRL; the engine then copies descriptor
// 0's transfer, follows its `next` pointer, copies the next transfer, and so
// on until a descriptor with `last` set. The CPU is only interrupted once, at
// the end of the whole chain.
//
// It has a slave register interface (mapped into the SoC address window
// 0x20..0x3F), a master read/write interface into the fabric's SRAM, and a
// descriptor read port. Together with the RV32I CPU it is the second bus
// master of the SoC. The fabric arbitrates DMA writes over host-bus writes
// while the GEMM is idle; the DMA's combinational read is separate from the
// bus read so the CPU can poll DMA_STATUS while a copy is running.
//
// On completion the engine raises an interrupt (dma_irq / status[2]) which
// stays asserted until the next start clears it.
//
// Slave interface is intentionally NARROW (5-bit address offset, 3-bit data)
// so every slave bit is toggle-coverable, matching the fabric's word-index
// philosophy. The master interface is a full 32-bit data path (real word
// copies); the testbench feeds diverse 32-bit source data so every master
// data bit toggles. Master byte addresses are word-aligned (addr[1:0]==0),
// so those two low address bits per master port are declared unreachable.
//
// Slave window offsets (byte address - 0x20):
//   0x00 DMA_SRC     [1:0] source window (0=A, 1=B, 2=C)
//   0x04 DMA_DST     [1:0] destination window (0=A, 1=B, 2=C)
//   0x08 DMA_SRCS    [2:0] source row stride (words)
//   0x0C DMA_DSTS    [2:0] destination row stride (words)
//   0x10 DMA_CTRL    [0] start (write pulse), [1] chain mode
//   0x14 DMA_STATUS  [0] busy, [1] done, [2] irq
//   0x18 DMA_HEAD    [1:0] first descriptor index (chain mode)
//
// Descriptor (one 32-bit word at 0x100 + 4*i; only the low 13 bits are used):
//   [1:0]   source window
//   [3:2]   destination window
//   [6:4]   source row stride (words)
//   [9:7]   destination row stride (words)
//   [10]    last (end of chain)
//   [12:11] next descriptor index
//
// The default stride is N (a contiguous row-major copy): on reset the engine
// is ready to do the same linear A->B memcpy as before.

module tiny_dma #(
    parameter int unsigned N  = 4,
    parameter int unsigned DW = 32
) (
    input  logic          clk,
    input  logic          rst_n,
    // ---- slave (register) interface; s_addr is the 5-bit offset within 0x20..0x3F ----
    input  logic          s_we,
    input  logic [4:0]    s_addr,
    input  logic [2:0]    s_wdata,
    output logic [2:0]    s_rdata,
    // ---- master interface into the fabric SRAM ----
    output logic          m_we,
    output logic [7:0]    m_waddr,
    output logic [DW-1:0] m_wdata,
    output logic [7:0]    m_raddr,
    input  logic [DW-1:0] m_rdata,
    // ---- interrupt: asserted on done, cleared on the next start ----
    output logic          dma_irq,
    // ---- descriptor read port (one word per index) ----
    output logic [1:0]    desc_raddr,
    input  logic [12:0]   desc_rdata
);

    localparam int unsigned IW = (N > 1) ? $clog2(N) : 1;

    logic [1:0]    src_sel;
    logic [1:0]    dst_sel;
    logic [2:0]    src_stride;
    logic [2:0]    dst_stride;
    logic          ctrl_start;
    logic          chain_mode;
    logic [1:0]    head;
    logic          busy;
    logic          done;
    logic          irq;
    logic [IW-1:0] row;
    logic [IW-1:0] col;
    logic [1:0]    desc_idx;

    // ---- current descriptor fields ----
    wire [1:0] d_src_sel    = desc_rdata[1:0];
    wire [1:0] d_dst_sel    = desc_rdata[3:2];
    wire [2:0] d_src_stride = desc_rdata[6:4];
    wire [2:0] d_dst_stride = desc_rdata[9:7];
    wire       d_last       = desc_rdata[10];
    wire [1:0] d_next       = desc_rdata[12:11];

    // In chain mode while running, the transfer fields come from the loaded
    // descriptor; otherwise they come from the slave registers.
    wire       chain = chain_mode && busy;
    wire [1:0] cur_src_sel    = chain ? d_src_sel    : src_sel;
    wire [1:0] cur_dst_sel    = chain ? d_dst_sel    : dst_sel;
    wire [2:0] cur_src_stride = chain ? d_src_stride : src_stride;
    wire [2:0] cur_dst_stride = chain ? d_dst_stride : dst_stride;

    // Word offset within the window: row*stride + col.
    wire [4:0] src_off = (5'(row) * 5'(cur_src_stride)) + 5'(col);
    wire [4:0] dst_off = (5'(row) * 5'(cur_dst_stride)) + 5'(col);

    assign m_we    = busy;
    // Word-aligned 32-bit copies: the byte address is always 4-aligned, so
    // m_waddr/m_raddr bits [1:0] are provably 0 (waived, not covered).
    // Window base for sel 0/1/2 is 0x40/0x80/0xC0 (0x40 + sel*0x40).
    assign m_waddr = 8'h40 + 8'(cur_dst_sel << 6) + (8'(dst_off) << 2);
    assign m_wdata = m_rdata;
    assign m_raddr = 8'h40 + 8'(cur_src_sel << 6) + (8'(src_off) << 2);
    assign dma_irq = irq;

    assign desc_raddr = desc_idx;

    // ---- register file ----
    always_ff @(posedge clk) begin
        if (!rst_n) begin
            src_sel    <= 2'b00;
            dst_sel    <= 2'b00;
            src_stride <= 3'(N);
            dst_stride <= 3'(N);
            ctrl_start <= 1'b0;
            chain_mode <= 1'b0;
            head       <= 2'b00;
        end else begin
            ctrl_start <= 1'b0;
            if (s_we) begin
                case (s_addr)
                    5'h0:  src_sel    <= s_wdata[1:0];
                    5'h4:  dst_sel    <= s_wdata[1:0];
                    5'h8:  src_stride <= s_wdata[2:0];
                    5'hC:  dst_stride <= s_wdata[2:0];
                    5'h10: begin
                        if (s_wdata[0]) ctrl_start <= 1'b1;
                        chain_mode <= s_wdata[1];
                    end
                    5'h18: head <= s_wdata[1:0];
                    default: ;
                endcase
            end
        end
    end

    // ---- copy sequencer: row-major, N words per row, strided rows ----
    always_ff @(posedge clk) begin
        if (!rst_n) begin
            row      <= {IW{1'b0}};
            col      <= {IW{1'b0}};
            busy     <= 1'b0;
            done     <= 1'b0;
            irq      <= 1'b0;
            desc_idx <= 2'b00;
        end else if (ctrl_start && !busy) begin
            row      <= {IW{1'b0}};
            col      <= {IW{1'b0}};
            busy     <= 1'b1;
            done     <= 1'b0;
            irq      <= 1'b0;
            desc_idx <= chain_mode ? head : 2'b00;
        end else if (busy) begin
            if (col == IW'(N-1)) begin
                if (row == IW'(N-1)) begin
                    if (chain && !d_last) begin
                        // Follow the chain to the next descriptor.
                        desc_idx <= d_next;
                        row      <= {IW{1'b0}};
                        col      <= {IW{1'b0}};
                    end else begin
                        busy <= 1'b0;
                        done <= 1'b1;
                        irq  <= 1'b1;
                    end
                end else begin
                    row <= row + 1'b1;
                    col <= {IW{1'b0}};
                end
            end else begin
                col <= col + 1'b1;
            end
        end
    end

    // ---- slave read-back ----
    always_comb begin
        s_rdata = 3'b000;
        case (s_addr)
            5'h0:  s_rdata = {1'b0, src_sel};
            5'h4:  s_rdata = {1'b0, dst_sel};
            5'h8:  s_rdata = src_stride;
            5'hC:  s_rdata = dst_stride;
            5'h18: s_rdata = {1'b0, head};
            5'h14: s_rdata = {irq, done, busy};
            default: ;
        endcase
    end

endmodule
