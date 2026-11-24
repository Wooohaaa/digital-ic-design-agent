#ifndef RSI_RV32_ACT_MODEL_TEST_H
#define RSI_RV32_ACT_MODEL_TEST_H

#define RVMODEL_BOOT
#define RVMODEL_HALT .global rsi_halt; rsi_halt: ebreak;

#define RVMODEL_DATA_BEGIN .pushsection .data; .balign 4; \
    .global begin_signature; begin_signature:
#define RVMODEL_DATA_END .balign 4; .global end_signature; end_signature:; .popsection

#define RVMODEL_IO_ASSERT_GPR_EQ(_scratch, _reg, _value)

#endif
