    .section .text.init
    .global rvtest_entry_point
rvtest_entry_point:
    la sp, stack_end
    call checksum
    la t0, begin_signature
    sd a0, 0(t0)
    .global rsi_halt
rsi_halt:
    ebreak

    .section .data
    .balign 8
    .global begin_signature
begin_signature:
    .dword 0
    .global end_signature
end_signature:
    .balign 16
    .zero 1024
stack_end:
