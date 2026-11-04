module luna_rv32(
 input wire clk, input wire rst_n, output wire [31:0] imem_addr, output wire imem_valid, input wire imem_ready,
 input wire [31:0] imem_rdata, output wire dmem_valid, output wire [3:0] dmem_wstrb,
 output wire [31:0] dmem_addr, output wire [31:0] dmem_wdata, input wire [31:0] dmem_rdata, input wire dmem_ready,
 output reg retire_valid, output reg [31:0] retire_pc, output reg [31:0] retire_insn,
 output reg [4:0] retire_rd, output reg [31:0] retire_wdata, output reg halted, output reg trap
);
 localparam [1:0] FETCH=0, DECODE=1, COMMIT=2, HALT=3;
 localparam [1:0] A_NONE=0, A_ALU=1, A_LOAD=2, A_STORE=3;
 reg [1:0] state, action;
 reg [31:0] pc, ir, ir_pc, next_pc, result_q, effaddr, store_q;
 reg [4:0] rd_q;
 reg rd_we;
 reg [2:0] mem_f3, store_size;
 reg [1:0] mem_offset;
 reg [31:0] regs[0:31];
 integer i;
 wire [6:0] opcode=ir[6:0], funct7=ir[31:25];
 wire [4:0] rd=ir[11:7], rs1=ir[19:15], rs2=ir[24:20];
 wire [2:0] funct3=ir[14:12];
 wire [31:0] a=(rs1==0)?32'b0:regs[rs1];
 wire [31:0] b=(rs2==0)?32'b0:regs[rs2];
 wire [31:0] ii={{20{ir[31]}},ir[31:20]};
 wire [31:0] si={{20{ir[31]}},ir[31:25],ir[11:7]};
 wire [31:0] bi={{19{ir[31]}},ir[31],ir[7],ir[30:25],ir[11:8],1'b0};
 wire [31:0] ui={ir[31:12],12'b0};
 wire [31:0] ji={{11{ir[31]}},ir[31],ir[19:12],ir[20],ir[30:21],1'b0};
 wire [31:0] shifted_load=dmem_rdata >> (mem_offset*8);
 reg [31:0] load_value;
 always @* begin
  case(mem_f3)
   3'b000: load_value={{24{shifted_load[7]}},shifted_load[7:0]};
   3'b001: load_value={{16{shifted_load[15]}},shifted_load[15:0]};
   3'b010: load_value=dmem_rdata;
   3'b100: load_value={24'b0,shifted_load[7:0]};
   3'b101: load_value={16'b0,shifted_load[15:0]};
   default: load_value=32'b0;
  endcase
 end
 assign imem_addr=pc;
 assign imem_valid=(state==FETCH)&&!halted&&!trap;
 assign dmem_valid=(state==COMMIT)&&!halted&&!trap&&(action==A_LOAD||action==A_STORE);
 assign dmem_addr=effaddr;
 assign dmem_wstrb=(dmem_valid&&action==A_STORE)?
  ((store_size==0)?(4'b0001<<mem_offset): (store_size==1)?(4'b0011<<mem_offset):
   (store_size==2)?4'b1111:4'b0000):4'b0000;
 assign dmem_wdata=store_q << (mem_offset*8);

 always @(posedge clk or negedge rst_n) begin
  if(!rst_n) begin
   state<=FETCH; action<=A_NONE; pc<=0; ir<=0; ir_pc<=0; next_pc<=0;
   result_q<=0; effaddr<=0; store_q<=0; rd_q<=0; rd_we<=0; mem_f3<=0; mem_offset<=0; store_size<=0;
   retire_valid<=0; retire_pc<=0; retire_insn<=0; retire_rd<=0; retire_wdata<=0; halted<=0; trap<=0;
   for(i=0;i<32;i=i+1) regs[i]<=0;
  end else begin
   retire_valid<=0; regs[0]<=0;
   if(halted||trap) state<=HALT;
   else case(state)
    FETCH: if(imem_ready) begin ir<=imem_rdata; ir_pc<=pc; state<=DECODE; end
    DECODE: begin
     next_pc<=ir_pc+4; action<=A_NONE; rd_q<=0; rd_we<=0; result_q<=0;
     case(opcode)
      7'b0110111: begin action<=A_ALU;rd_q<=rd;rd_we<=rd!=0;result_q<=ui;state<=COMMIT;end
      7'b0010111: begin action<=A_ALU;rd_q<=rd;rd_we<=rd!=0;result_q<=ir_pc+ui;state<=COMMIT;end
      7'b1101111: if((ir_pc+ji)&3) begin halted<=1;trap<=1;state<=HALT;end else begin action<=A_ALU;rd_q<=rd;rd_we<=rd!=0;result_q<=ir_pc+4;next_pc<=ir_pc+ji;state<=COMMIT;end
      7'b1100111: if(funct3!=0) begin halted<=1;trap<=1;state<=HALT;end else if((((a+ii)&32'hffff_fffe)&3)!=0) begin halted<=1;trap<=1;state<=HALT;end else begin action<=A_ALU;rd_q<=rd;rd_we<=rd!=0;result_q<=ir_pc+4;next_pc<=(a+ii)&32'hffff_fffe;state<=COMMIT;end
      7'b1100011: begin
       case(funct3)
        0,1,4,5,6,7: begin
         if (((funct3==0)&&(a==b))||((funct3==1)&&(a!=b))||((funct3==4)&&($signed(a)<$signed(b)))||((funct3==5)&&($signed(a)>=$signed(b)))||((funct3==6)&&(a<b))||((funct3==7)&&(a>=b))) begin
          if((ir_pc+bi)&3) begin halted<=1;trap<=1;state<=HALT;end else begin next_pc<=ir_pc+bi;state<=COMMIT;end
         end else state<=COMMIT;
        end
        default: begin halted<=1;trap<=1;state<=HALT;end
       endcase
      end
      7'b0000011: begin
       if((funct3==0)||(funct3==1)||(funct3==2)||(funct3==4)||(funct3==5)) begin
        effaddr<=a+ii;mem_offset<=(a+ii)&3;mem_f3<=funct3;action<=A_LOAD;rd_q<=rd;rd_we<=rd!=0;
        if(((funct3==1)||(funct3==5))&&((a+ii)&1)) begin halted<=1;trap<=1;state<=HALT;end
        else if((funct3==2)&&((a+ii)&3)) begin halted<=1;trap<=1;state<=HALT;end else state<=COMMIT;
       end else begin halted<=1;trap<=1;state<=HALT;end
      end
      7'b0100011: begin
       if((funct3==0)||(funct3==1)||(funct3==2)) begin
        effaddr<=a+si;mem_offset<=(a+si)&3;mem_f3<=funct3;store_size<=funct3;store_q<=b;action<=A_STORE;
        if((funct3==1)&&((a+si)&1)) begin halted<=1;trap<=1;state<=HALT;end
        else if((funct3==2)&&((a+si)&3)) begin halted<=1;trap<=1;state<=HALT;end else state<=COMMIT;
       end else begin halted<=1;trap<=1;state<=HALT;end
      end
      7'b0010011: begin
       case(funct3)
        0: begin action<=A_ALU;rd_q<=rd;rd_we<=rd!=0;result_q<=a+ii;state<=COMMIT;end
        2: begin action<=A_ALU;rd_q<=rd;rd_we<=rd!=0;result_q<=$signed(a)<$signed(ii);state<=COMMIT;end
        3: begin action<=A_ALU;rd_q<=rd;rd_we<=rd!=0;result_q<=a<ii;state<=COMMIT;end
        4: begin action<=A_ALU;rd_q<=rd;rd_we<=rd!=0;result_q<=a^ii;state<=COMMIT;end
        6: begin action<=A_ALU;rd_q<=rd;rd_we<=rd!=0;result_q<=a|ii;state<=COMMIT;end
        7: begin action<=A_ALU;rd_q<=rd;rd_we<=rd!=0;result_q<=a&ii;state<=COMMIT;end
        1: if(funct7!=0) begin halted<=1;trap<=1;state<=HALT;end else begin action<=A_ALU;rd_q<=rd;rd_we<=rd!=0;result_q<=a<<ir[24:20];state<=COMMIT;end
        5: if((funct7!=0)&&(funct7!=7'b0100000)) begin halted<=1;trap<=1;state<=HALT;end
           else if(funct7==7'b0100000) begin action<=A_ALU;rd_q<=rd;rd_we<=rd!=0;result_q<=$signed(a)>>>ir[24:20];state<=COMMIT;end
           else begin action<=A_ALU;rd_q<=rd;rd_we<=rd!=0;result_q<=a>>ir[24:20];state<=COMMIT;end
        default: begin halted<=1;trap<=1;state<=HALT;end
       endcase
      end
      7'b0110011: begin
       case({funct7,funct3})
        {7'b0000000,3'b000}: begin action<=A_ALU;rd_q<=rd;rd_we<=rd!=0;result_q<=a+b;state<=COMMIT;end
        {7'b0100000,3'b000}: begin action<=A_ALU;rd_q<=rd;rd_we<=rd!=0;result_q<=a-b;state<=COMMIT;end
        {7'b0000000,3'b001}: begin action<=A_ALU;rd_q<=rd;rd_we<=rd!=0;result_q<=a<<b[4:0];state<=COMMIT;end
        {7'b0000000,3'b010}: begin action<=A_ALU;rd_q<=rd;rd_we<=rd!=0;result_q<=$signed(a)<$signed(b);state<=COMMIT;end
        {7'b0000000,3'b011}: begin action<=A_ALU;rd_q<=rd;rd_we<=rd!=0;result_q<=a<b;state<=COMMIT;end
        {7'b0000000,3'b100}: begin action<=A_ALU;rd_q<=rd;rd_we<=rd!=0;result_q<=a^b;state<=COMMIT;end
        {7'b0000000,3'b101}: begin action<=A_ALU;rd_q<=rd;rd_we<=rd!=0;result_q<=a>>b[4:0];state<=COMMIT;end
        {7'b0100000,3'b101}: begin action<=A_ALU;rd_q<=rd;rd_we<=rd!=0;result_q<=$signed(a)>>>b[4:0];state<=COMMIT;end
        {7'b0000000,3'b110}: begin action<=A_ALU;rd_q<=rd;rd_we<=rd!=0;result_q<=a|b;state<=COMMIT;end
        {7'b0000000,3'b111}: begin action<=A_ALU;rd_q<=rd;rd_we<=rd!=0;result_q<=a&b;state<=COMMIT;end
        default: begin halted<=1;trap<=1;state<=HALT;end
       endcase
      end
      7'b0001111: if(funct3==0) state<=COMMIT; else begin halted<=1;trap<=1;state<=HALT;end
      7'b1110011: if(ir==32'h00100073) state<=COMMIT; else begin halted<=1;trap<=1;state<=HALT;end
      default: begin halted<=1;trap<=1;state<=HALT;end
     endcase
    end
    COMMIT: begin
     if((action==A_LOAD||action==A_STORE)&&!dmem_ready) begin
      state<=COMMIT;
     end else begin
      if(action==A_LOAD) begin if(rd_we&&rd_q!=0) regs[rd_q]<=load_value; end
      else if(action==A_ALU && rd_we&&rd_q!=0) regs[rd_q]<=result_q;
      pc<=next_pc;retire_valid<=1;retire_pc<=ir_pc;retire_insn<=ir;
      retire_rd<=rd_we?rd_q:0;
      if(action==A_LOAD&&rd_we) retire_wdata<=load_value;
      else if(action==A_ALU&&rd_we) retire_wdata<=result_q; else retire_wdata<=0;
      if(ir==32'h00100073) halted<=1;
      state<=FETCH;
     end
    end
    default: state<=HALT;
   endcase
  end
 end
endmodule
