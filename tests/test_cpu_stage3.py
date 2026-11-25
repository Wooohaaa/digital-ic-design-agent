from pathlib import Path
import shutil

import pytest

from fpga_agent.cpu_stage3 import (
    IMMEDIATE_OPS, OPS, PROJECT, REGISTER_OPS, RTL, SHIFT_OPS, TB,
    _simulate, _stimulus_and_reference, encode, first_difference, load_engine, make_programs,
)


def test_integer_corpus_encodings_match_llvm_assembly():
    # Clang RV64I .text hex, in OPS order. Each shift uses its largest legal immediate.
    words = """
        002081b3 402081b3 002091b3 0020a1b3 0020b1b3 0020c1b3 0020d1b3 4020d1b3
        0020e1b3 0020f1b3 002081bb 402081bb 002091bb 0020d1bb 4020d1bb
        fff08193 fff0a193 fff0b193 fff0c193 fff0e193 fff0f193 fff0819b
        03f09193 03f0d193 43f0d193 01f0919b 01f0d19b 41f0d19b 800001b7 80000197
    """
    actual = []
    for op in OPS:
        if op in REGISTER_OPS:
            operands = (3, 1, 2)
        elif op in IMMEDIATE_OPS:
            operands = (3, 1, -1)
        elif op in SHIFT_OPS:
            operands = (3, 1, SHIFT_OPS[op][3] - 1)
        else:
            operands = (3, 0, 0x80000)
        actual.append(f"{encode(op, *operands):08x}")
    assert actual == words.split()


@pytest.mark.skipif(not shutil.which("iverilog") or not shutil.which("vvp"), reason="Icarus tools unavailable")
def test_reset_and_reserved_instruction_retirement_against_rv64_engine(tmp_path):
    pytest.importorskip("unicorn")
    engine, registers, _ = load_engine()
    programs = [program for program in make_programs(1) if program.name in {
        "all-registers", "all-registers-after-reset", "word-shift-bit5",
    }]
    assert len(programs) == 3
    expected, terminal_lines = _stimulus_and_reference(programs, tmp_path, engine, registers)
    assert len(terminal_lines) == 1
    project = Path(__file__).resolve().parents[1] / PROJECT
    actual = _simulate(tmp_path, "rtl", project / RTL, project / TB)
    assert first_difference(expected, actual) is None
