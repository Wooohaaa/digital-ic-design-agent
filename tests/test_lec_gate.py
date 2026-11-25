from __future__ import annotations

import copy
import json
from unittest.mock import patch

import pytest

from fpga_agent.cli import do_td, ensure_required_lec
from fpga_agent.lec import find_yosys, lec_valid, proof_top, run_lec
from fpga_agent.project import create_project


def test_required_lec_blocks_every_td_entry_before_vendor_tool(tmp_path):
    project = create_project('gate', tmp_path / 'gate')
    manifest = json.loads((project / 'project.json').read_text())
    manifest['formal_equivalence'] = {'required': True, 'method': 'direct'}
    (project / 'project.json').write_text(json.dumps(manifest))
    (project / 'reports').mkdir()
    (project / 'reports/simulation.json').write_text('{}')
    with patch('fpga_agent.cli.simulation_valid', return_value=(True, 'test stub')), \
         patch('fpga_agent.lec.find_yosys', return_value=None), \
         patch('fpga_agent.cli.run_td', side_effect=AssertionError('must not invoke vendor tool')):
        assert do_td(project, 'synth', None) == 2
    assert json.loads((project / 'reports/lec.json').read_text())['status'] == 'blocked'


def test_nonrequired_lec_does_not_run_a_tool(tmp_path):
    (tmp_path / 'project.json').write_text(json.dumps({'top_module': 'core'}))
    with patch('fpga_agent.lec.run_lec', side_effect=AssertionError('not requested')):
        assert ensure_required_lec(tmp_path) == 0


def test_target_identifier_must_be_valid():
    with pytest.raises(ValueError):
        proof_top({'top_module': 'wrapper', 'formal_equivalence': {'target_top': 'core; shell'}})


@pytest.mark.skipif(find_yosys() is None, reason='Yosys integration tool not installed')
def test_core_scope_and_real_cached_proof_invalidate_on_source_or_target_change(tmp_path):
    (tmp_path / 'rtl').mkdir()
    source = tmp_path / 'rtl/core.v'
    source.write_text('''module core(input [7:0] a,b, output [7:0] y);
assign y=a+b; endmodule
module wrapper(input [7:0] a,b, output [7:0] y);
wire [7:0] value; core u(a,b,value); assign y=~value; endmodule''')
    manifest = {'top_module': 'wrapper', 'rtl_sources': ['rtl/core.v'],
                'formal_equivalence': {'required': True, 'method': 'state-bijection', 'target_top': 'core'}}
    (tmp_path / 'project.json').write_text(json.dumps(manifest))
    report = run_lec(tmp_path)
    assert report['status'] == 'pass', report['reason']
    assert (report['design_top'], report['proof_top']) == ('wrapper', 'core')
    with patch('fpga_agent.lec.run_lec', side_effect=AssertionError('reuse current proof')):
        assert ensure_required_lec(tmp_path) == 0
    changed = copy.deepcopy(report)
    changed['proof_top'] = 'wrapper'
    assert not lec_valid(tmp_path, changed)[0]
    source.write_text(source.read_text()+'\n// changed input\n')
    with patch('fpga_agent.lec.run_lec', wraps=run_lec) as rerun:
        assert ensure_required_lec(tmp_path) == 0
        rerun.assert_called_once()
    updated = json.loads((tmp_path / 'reports/lec.json').read_text())
    manifest['formal_equivalence']['target_top'] = 'wrapper'
    (tmp_path / 'project.json').write_text(json.dumps(manifest))
    assert not lec_valid(tmp_path, updated)[0]
