from pathlib import Path
import shutil

import pytest

from fpga_agent.cpu_eei import PREFIX, _observe_fault, fault_cases, run_fault_matrix
from fpga_agent.cpu_external import load_engine
from fpga_agent.cpu_stage2 import BRANCH_OUTCOMES, INSTRUCTIONS, MEMORY_LANES, REQUIRED_COVERAGE
from fpga_agent.riscv import corpus, run_reference


def test_frozen_coverage_has_39_retiring_ops_and_all_legal_lanes():
    reference = set().union(*(run_reference(program).coverage for program in corpus(64)))
    assert len(INSTRUCTIONS) == 39 and 'ECALL' not in INSTRUCTIONS
    assert len(BRANCH_OUTCOMES) == 12
    assert len(MEMORY_LANES) == 20
    assert REQUIRED_COVERAGE <= reference


def test_external_eei_keeps_native_exception_and_policy_evidence_distinct():
    pytest.importorskip('unicorn')
    engine, registers, _ = load_engine()
    cases = {fault.name: fault for fault in fault_cases()}
    assert len(cases) == 32
    ecall = _observe_fault(cases['ecall'], engine, registers)
    assert ecall['native_exception'] == [8] and ecall['fault_pc'] == 4*len(PREFIX)
    jalr = _observe_fault(cases['jalr_target_bit0'], engine, registers)
    assert jalr['native_exception'] == [] and jalr['next_pc'] == 2
    sw = _observe_fault(cases['sw_lane2'], engine, registers)
    assert sw['native_exception'] == [] and sw['detail'] == [0x1002, 4]


@pytest.mark.skipif(not (shutil.which('iverilog') and shutil.which('vvp')), reason='Icarus required')
def test_trapping_store_must_not_escape_to_memory(tmp_path):
    pytest.importorskip('unicorn')
    original = Path(__file__).resolve().parents[1]/'benchmarks/rv32_single/rtl/rv32_core.v'
    source = original.read_bytes()
    anchor = b'&& legal && !misaligned && (is_load || is_store)'
    assert source.count(anchor) == 1
    mutant = tmp_path/'mutant.v'
    mutant.write_bytes(source.replace(anchor, b'&& legal && (is_load || is_store)'))
    report = run_fault_matrix({'single': original, 'mutant': mutant}, tmp_path/'faults')
    assert report['status'] == 'fail'
    assert all(case['status'] == 'pass' for case in report['implementations']['single']['cases'].values())
    assert 'stores mismatch' in report['implementations']['mutant']['cases']['sw_lane2']['error']
