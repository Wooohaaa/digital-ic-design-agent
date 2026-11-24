import os
from pathlib import Path
import shutil
import struct

import pytest

from fpga_agent.cpu_act import DATA_BASE, Layout, parse_layout, parse_signature, run_act_matrix


def test_act_layout_requires_the_real_ebreak_and_signature_bounds():
    symbols = '\n'.join(['00000000 T rvtest_entry_point', '00000004 T rsi_halt', '00000008 T rsi_text_end',
                         f'{DATA_BASE:08x} D begin_signature', f'{DATA_BASE+8:08x} D end_signature'])
    image = (b'\0'*4 + struct.pack('<I', 0x00100073)).ljust(DATA_BASE+8, b'\0')
    assert parse_layout(symbols, image) == Layout(4, DATA_BASE, DATA_BASE+8, 8)
    with pytest.raises(ValueError, match='ELF layout'):
        parse_layout(symbols, b'\0'*len(image))
    with pytest.raises(ValueError, match='ELF layout'):
        parse_layout(symbols, image[:-1])


def test_act_signature_count_order_and_stop_are_checked():
    layout = Layout(4, 0x10000, 0x10008, 8)
    row0 = 'SIGNATURE 00010000 00000001\n'
    row1 = 'SIGNATURE 00010004 aabbccdd\n'
    stop = 'DONE 12\n'
    assert parse_signature(row0+row1+stop, layout) == ([1, 0xaabbccdd], 12)
    for text in (row0+stop, row1+row0+stop, row0+row1, row0+row1+stop+stop, row0+row0+stop, stop+row0+row1,
                 row0+'SIGNATURE 00010004 xxxxxxxx\n'+stop):
        with pytest.raises(ValueError):
            parse_signature(text, layout)


def test_act_rejects_unpinned_upstream_input(tmp_path):
    suite = tmp_path/'suite'
    target = suite/'riscv-test-suite/env/arch_test.h'
    target.parent.mkdir(parents=True)
    target.write_text('modified upstream file\n', encoding='ascii')
    report = run_act_matrix(tmp_path, suite, tmp_path/'matrix', 'unused-clang', ('add-01',))
    assert report['status'] == 'blocked'
    assert 'Upstream source differs' in report['error']


def test_act_distinguishes_real_shift_fault_from_a_passing_core(tmp_path):
    suite, clang = os.environ.get('RSI_ACT_SUITE'), os.environ.get('RSI_ACT_CLANG')
    if not (suite and clang and shutil.which('iverilog') and shutil.which('vvp')):
        pytest.skip('Set RSI_ACT_SUITE and RSI_ACT_CLANG with the optional Unicorn and Icarus tools')
    pytest.importorskip('unicorn')
    source = Path(__file__).resolve().parents[1]/'benchmarks'
    for core in ('single', 'multi', 'pipeline'):
        relative = Path(f'benchmarks/rv32_{core}/rtl/rv32_core.v')
        target = tmp_path/relative
        target.parent.mkdir(parents=True)
        original = (source/relative.relative_to('benchmarks')).read_bytes()
        if core == 'single':
            anchor = b'$signed(src1) >>> insn[24:20]'
            assert original.count(anchor) == 1
            original = original.replace(anchor, b'src1 >> insn[24:20]')
        target.write_bytes(original)
    report = run_act_matrix(tmp_path, Path(suite), tmp_path/'matrix', clang, ('srai-01',))
    assert report['status'] == 'fail'
    case = report['cores']['single']['cases']['srai-01']
    assert case['status'] == 'fail'
    assert 'Signature mismatch at' in case['error']
    assert all(report['cores'][core]['cases']['srai-01']['status'] == 'pass'
               for core in ('multi', 'pipeline'))
