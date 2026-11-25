module sv39_walker (
    input wire clk,
    input wire rst_n,
    input wire request,
    input wire [63:0] virtual_address,
    input wire [43:0] root_ppn,
    input wire [1:0] access_kind, // read, write, execute
    input wire user_mode, sum, mxr,
    output wire ready,
    output wire pte_request,
    output wire [55:0] pte_address,
    input wire pte_valid,
    input wire pte_access_fault,
    input wire [63:0] pte,
    output reg done,
    output reg [1:0] fault, // none, page fault, PTE access fault
    output reg [55:0] physical_address
);
    reg active;
    reg [1:0] level;
    reg [38:0] address;
    reg [43:0] table_ppn;
    reg [1:0] kind;
    reg user_access, allow_user_data, execute_as_read;
    wire [8:0] vpn = level == 2 ? address[38:30] :
                     level == 1 ? address[29:21] : address[20:12];
    assign ready = !active;
    assign pte_request = active;
    assign pte_address = {table_ppn, vpn, 3'b0};

    wire [43:0] ppn = pte[53:10];
    wire readable = pte[1];
    wire writable = pte[2];
    wire executable = pte[3];
    wire leaf = readable || executable;
    wire bad_pte = !pte[0] || (!readable && writable) || pte[63:54] != 0;
    wire can_access_page = user_access ? pte[4] :
                           (!pte[4] || (kind != 2 && allow_user_data));
    wire can_access_type = kind == 0 ? (readable || (execute_as_read && executable)) :
                           kind == 1 ? writable : executable;
    wire bad_superpage = (level == 2 && ppn[17:0] != 0) || (level == 1 && ppn[8:0] != 0);
    wire [43:0] result_ppn = level == 2 ? {ppn[43:18], address[29:12]} :
                             level == 1 ? {ppn[43:9], address[20:12]} : ppn;
    wire leaf_fault = !can_access_page || !can_access_type || bad_superpage || !pte[6] ||
                      (kind == 1 && !pte[7]);

    always @(posedge clk) begin
        if (!rst_n) begin
            active <= 0;
            level <= 0;
            address <= 0;
            table_ppn <= 0;
            kind <= 0;
            user_access <= 0;
            allow_user_data <= 0;
            execute_as_read <= 0;
            done <= 0;
            fault <= 0;
            physical_address <= 0;
        end else begin
            done <= 0;
            if (ready && request) begin
                fault <= 0;
                physical_address <= 0;
                if (virtual_address[63:39] != {25{virtual_address[38]}} || access_kind == 3) begin
                    done <= 1;
                    fault <= 1;
                end else begin
                    active <= 1;
                    level <= 2;
                    address <= virtual_address[38:0];
                    table_ppn <= root_ppn;
                    kind <= access_kind;
                    user_access <= user_mode;
                    allow_user_data <= sum;
                    execute_as_read <= mxr;
                end
            end else if (active && pte_valid) begin
                if (!pte_access_fault && !bad_pte && !leaf && level != 0 && (pte & 64'hd0) == 0) begin
                    level <= level - 1;
                    table_ppn <= ppn;
                end else begin
                    active <= 0;
                    done <= 1;
                    if (pte_access_fault)
                        fault <= 2;
                    else if (bad_pte || !leaf || leaf_fault)
                        fault <= 1;
                    else
                        physical_address <= {result_ppn, address[11:0]};
                end
            end
        end
    end
endmodule
