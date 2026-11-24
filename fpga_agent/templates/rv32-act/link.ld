OUTPUT_ARCH(riscv)
ENTRY(rvtest_entry_point)

PHDRS
{
    rom PT_LOAD FLAGS(5);
    ram PT_LOAD FLAGS(6);
}

SECTIONS
{
    .text 0 : { *(.text.init) *(.text .text.*) *(.rodata .rodata.*) . = ALIGN(4); rsi_text_end = .; } :rom
    .data 0x200000 : { *(.data .data.*) *(.sdata .sdata.*) } :ram
    .bss : { *(.bss .bss.*) *(.sbss .sbss.*) *(COMMON) } :ram
}

ASSERT(SIZEOF(.text) <= 0x200000, "ACT text exceeds ROM")
ASSERT(ADDR(.bss) + SIZEOF(.bss) <= 0x210000, "ACT data exceeds RAM")
