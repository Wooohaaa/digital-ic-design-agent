from __future__ import annotations

import unittest
import tempfile
import json
from pathlib import Path

from fpga_agent.cpu_bench import parse_and_compare,prepare_project,run_benchmark
from fpga_agent.riscv import EBREAK, Program, enc_i, enc_s, li, run_reference


def valid_log(expected) -> str:
    lines = []
    for pc, insn, rd, data in expected.trace:
        lines.append(f"RETIRE {pc:08x} {insn:08x} {rd:02x} {data:08x}")
    for address, mask, value in expected.stores:
        lines.append(f"STORE {address:08x} {mask:01x} {value:08x}")
    lines.extend(f"MEM {address:08x} {value:08x}" for address, value in expected.memory.items())
    lines.append(f"STOP {int(expected.trap):01x} 99")
    return "\n".join(lines) + "\n"


class ParseAndCompareTests(unittest.TestCase):
    def test_modified_soc_testbench_cannot_be_blessed_by_manifest_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project=Path(tmp);(project/'rtl').mkdir()
            (project/'rtl/rv32_core.v').write_text('module rv32_core; endmodule')
            prepare_project(project)
            (project/'tb/tb_soc.v').write_text('forged PASS')
            manifest=json.loads((project/'project.json').read_text())
            manifest['trusted_testbench_hashes']={}
            (project/'project.json').write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError,'SoC testbench'):
                run_benchmark(project,0)

    def test_complete_trusted_testbench_fingerprint_list_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project=Path(tmp);(project/'rtl').mkdir()
            (project/'rtl/rv32_core.v').write_text('module rv32_core; endmodule')
            prepare_project(project)
            manifest=json.loads((project/'project.json').read_text())
            manifest['trusted_testbench_hashes']={}
            (project/'project.json').write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError,'Complete trusted'):
                run_benchmark(project,0)

    def setUp(self) -> None:
        words = li(1, 0x1000) + li(2, 0x12345678)
        words += [enc_s(2, 1, 2, 0), enc_i(3, 3, 2, 1, 0), EBREAK]
        self.expected = run_reference(Program("store_load_stop", words))
        self.good = valid_log(self.expected)

    def test_accepts_matching_retirement_store_stop_and_memory(self) -> None:
        metrics = parse_and_compare(self.good, self.expected)
        self.assertEqual(metrics["retired"], len(self.expected.trace))
        self.assertEqual(metrics["stores"], len(self.expected.stores))

    def test_rejects_corrupted_retirement(self) -> None:
        lines = self.good.splitlines()
        lines[0] = lines[0].replace("00000000", "00000004", 1)
        with self.assertRaisesRegex(AssertionError, "retirement mismatch"):
            parse_and_compare("\n".join(lines), self.expected)

    def test_rejects_corrupted_store(self) -> None:
        lines = self.good.splitlines()
        store_index = next(i for i, line in enumerate(lines) if line.startswith("STORE "))
        lines[store_index] = lines[store_index].replace("f", "e", 1)
        with self.assertRaisesRegex(AssertionError, "stores mismatch"):
            parse_and_compare("\n".join(lines), self.expected)

    def test_rejects_missing_stop_record(self) -> None:
        truncated = "\n".join(line for line in self.good.splitlines() if not line.startswith("STOP "))
        with self.assertRaisesRegex(AssertionError, "halting/trap mismatch"):
            parse_and_compare(truncated, self.expected)

    def test_rejects_corrupted_final_memory(self) -> None:
        lines = self.good.splitlines()
        mem_index = next(i for i, line in enumerate(lines) if line.startswith("MEM "))
        fields = lines[mem_index].split()
        fields[2] = f"{int(fields[2], 16) ^ 1:08x}"
        lines[mem_index] = " ".join(fields)
        with self.assertRaisesRegex(AssertionError, "final data memory mismatch"):
            parse_and_compare("\n".join(lines), self.expected)


if __name__ == "__main__":
    unittest.main()
