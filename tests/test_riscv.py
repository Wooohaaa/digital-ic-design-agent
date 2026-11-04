from __future__ import annotations

import unittest

from fpga_agent.riscv import EBREAK, Program, enc_i, enc_j, enc_r, enc_s, li, run_reference


class ReferenceExpectedValueTests(unittest.TestCase):
    def test_reserved_fence_modes_and_tso_are_ignored_by_base_implementation(self) -> None:
        result=run_reference(Program('fence_forward_compatibility',[0xfabf8f8f,0x8330000f,EBREAK]))
        self.assertFalse(result.trap)
        self.assertEqual([row[2:] for row in result.trace],[(0,0),(0,0),(0,0)])
    def test_srai_sign_extends_and_sltiu_sign_extends_its_immediate(self) -> None:
        words = li(1, 0x80000000) + [enc_i(0x13, 2, 5, 1, 0x41F)]
        words += li(3, 0x1000) + [enc_i(0x13, 4, 3, 3, -2048), EBREAK]
        result = run_reference(Program("signed_shift_and_unsigned_compare", words))
        self.assertFalse(result.trap)
        self.assertEqual(result.registers[2], 0xFFFFFFFF)
        self.assertEqual(result.registers[4], 1)  # 0x1000 < sign-extended 0xfffff800
        sltiu = next(row for row in result.trace if row[1] == enc_i(0x13, 4, 3, 3, -2048))
        self.assertEqual(sltiu[2:], (4, 1))

    def test_x0_discards_register_and_load_results_in_trace(self) -> None:
        words = li(1, 0x1000) + li(2, 0x12345678)
        words += [enc_s(2, 1, 2, 0), enc_i(3, 0, 2, 1, 0), enc_i(0x13, 3, 0, 0, 7), EBREAK]
        result = run_reference(Program("writes_to_x0", words))
        self.assertFalse(result.trap)
        self.assertEqual(result.registers[0], 0)
        x0_load = next(row for row in result.trace if row[1] == enc_i(3, 0, 2, 1, 0))
        self.assertEqual(x0_load[2:], (0, 0))
        self.assertEqual(result.registers[3], 7)

    def test_signed_byte_load_and_byte_store_lanes(self) -> None:
        words = li(10, 0x1000) + li(1, 0x80FF7F01)
        words += [enc_s(2, 10, 1, 0), enc_i(3, 2, 0, 10, 3), enc_i(3, 3, 4, 10, 3),
                  enc_s(0, 10, 1, 1), enc_i(3, 4, 0, 10, 1), EBREAK]
        result = run_reference(Program("byte_lanes", words))
        self.assertFalse(result.trap)
        self.assertEqual(result.registers[2], 0xFFFFFF80)
        self.assertEqual(result.registers[3], 0x80)
        self.assertEqual(result.registers[4], 1)
        self.assertEqual(result.stores[-1], (0x1001, 0b0010, 0x00000100))
        self.assertEqual(result.memory[0x1000], 0x80FF0101)

    def test_jalr_reads_old_rs1_when_rd_equals_rs1(self) -> None:
        words = li(1, 16)
        words += [enc_i(0x67, 1, 0, 1, 0), enc_i(0x13, 2, 0, 0, 99),
                  enc_i(0x13, 2, 0, 1, 0), EBREAK]
        result = run_reference(Program("jalr_same_rd_rs1", words))
        self.assertFalse(result.trap)
        jalr = next(row for row in result.trace if row[1] == enc_i(0x67, 1, 0, 1, 0))
        self.assertEqual(jalr, (8, enc_i(0x67, 1, 0, 1, 0), 1, 12))
        self.assertNotIn(12, [row[0] for row in result.trace])
        self.assertEqual(result.trace[3][0], 16)
        self.assertEqual(result.registers[2], 12)

    def test_misaligned_store_and_jump_trap_without_side_effects(self) -> None:
        words = li(1, 0x1000) + li(2, 0xAABBCCDD) + [enc_s(1, 1, 2, 1), EBREAK]
        result = run_reference(Program("misaligned_store", words))
        self.assertTrue(result.trap)
        self.assertEqual(result.stores, [])
        self.assertEqual(result.memory[0x1000], 0)
        self.assertNotIn(EBREAK, [row[1] for row in result.trace])

        jump = run_reference(Program("misaligned_jal", [enc_j(5, 2), EBREAK]))
        self.assertTrue(jump.trap)
        self.assertEqual(jump.trace, [])
        self.assertEqual(jump.registers[5], 0)

    def test_ecall_traps_without_retirement_but_ebreak_retires_then_stops(self) -> None:
        ecall = run_reference(Program("ecall", [0x00000073, EBREAK]))
        self.assertTrue(ecall.trap)
        self.assertEqual(ecall.trace, [])

        ebreak = run_reference(Program("ebreak", [EBREAK, 0xFFFFFFFF]))
        self.assertFalse(ebreak.trap)
        self.assertEqual(ebreak.trace, [(0, EBREAK, 0, 0)])


if __name__ == "__main__":
    unittest.main()
