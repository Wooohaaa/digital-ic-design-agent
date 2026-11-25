from pathlib import Path
import shutil

import pytest

from fpga_agent.cpu_external import load_engine, run_matrix, run_unicorn
from fpga_agent.riscv import EBREAK, Program


@pytest.fixture
def unicorn_engine():
    pytest.importorskip('unicorn')
    engine, registers, _ = load_engine()
    return engine, registers


def test_unicorn_reads_registers_and_normalizes_store_lanes(unicorn_engine):
    program = Program('byte_lane', [0xfff00093, 0x00001137, 0x001101a3,
                                    0x00314203, 0x00310283, EBREAK])
    expected = run_unicorn(program, *unicorn_engine)
    assert expected.trace == [
        (0, 0xfff00093, 1, 0xffffffff),
        (4, 0x00001137, 2, 0x1000),
        (8, 0x001101a3, 0, 0),
        (12, 0x00314203, 4, 0xff),
        (16, 0x00310283, 5, 0xffffffff),
        (20, EBREAK, 0, 0),
    ]
    assert expected.stores == [(0x1003, 8, 0xff000000)]
    assert expected.memory[0x1000] == 0xff000000


def test_unicorn_scope_rejects_misalignment_and_missing_termination(unicorn_engine):
    unaligned = Program('unaligned', [0x00001137, 0x00112083, EBREAK])
    with pytest.raises(ValueError, match='outside aligned fixture RAM'):
        run_unicorn(unaligned, *unicorn_engine)
    with pytest.raises(ValueError, match='did not reach the termination marker'):
        run_unicorn(Program('loop', [0x0000006f, EBREAK], max_steps=3), *unicorn_engine)


@pytest.mark.skipif(not (shutil.which('iverilog') and shutil.which('vvp')), reason='Icarus required')
def test_external_matrix_detects_arithmetic_and_store_faults(unicorn_engine, tmp_path):
    source = Path(__file__).resolve().parents[1]/'benchmarks'
    for core in ('single', 'multi', 'pipeline'):
        for relative in ('rtl/rv32_core.v', 'tb/tb_cpu.v'):
            target = tmp_path/f'benchmarks/rv32_{core}'/relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source/f'rv32_{core}'/relative, target)
    single = tmp_path/'benchmarks/rv32_single/rtl/rv32_core.v'
    old = single.read_bytes()
    shift = b'$signed(src1) >>> insn[24:20]'
    assert old.count(shift) == 1
    single.write_bytes(old.replace(shift, b'src1 >> insn[24:20]'))
    report = run_matrix(tmp_path, tmp_path/'arithmetic', seeds=0)
    assert report['status'] == 'fail'
    assert report['cores']['single'][-1]['name'] == 'alu_edges'
    assert 'retirement mismatch' in report['cores']['single'][-1]['error']
    assert all(row['status'] == 'pass' for row in report['cores']['multi'])
    assert all(row['status'] == 'pass' for row in report['cores']['pipeline'])

    # Redirect SB to lane 0. The directed case does not read back those bytes.
    store = b"4'b0001 << eff_addr[1:0]"
    assert old.count(store) == 1
    single.write_bytes(old.replace(store, b"4'b0001"))
    report = run_matrix(tmp_path, tmp_path/'store', seeds=0)
    assert report['status'] == 'fail'
    assert report['cores']['single'][-1]['name'] == 'memory_lanes'
    assert 'stores mismatch' in report['cores']['single'][-1]['error']
