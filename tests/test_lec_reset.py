"""Post-reset equality classes must prove every member, not just cross outputs."""
from __future__ import annotations

import copy
import json
from unittest.mock import patch

import pytest

from fpga_agent.lec import find_yosys, lec_valid, proof_method, run_lec


GOLD = '''module core(input clk, rst_n, en, output [9:0] y);
reg [7:0] count;
reg [1:0] low;
always @(posedge clk or negedge rst_n) begin
 if(!rst_n) begin count<=0; low<=0; end
 else if(en) begin count<=count+1; low<=count[1:0]+1; end
end
assign y={count,low}; endmodule
'''
GATE = '''module core(input clk, rst_n, en, output [9:0] y);
reg [7:0] count;
wire [1:0] low=count[1:0];
always @(posedge clk or negedge rst_n)
 if(!rst_n) count<=0; else if(en) count<=count+1;
assign y={count,low}; endmodule
'''


@pytest.fixture
def project(tmp_path):
    (tmp_path / 'rtl').mkdir()
    (tmp_path / 'rtl/core.v').write_text(GOLD)
    (tmp_path / 'candidate.v').write_text(GATE)
    (tmp_path / 'project.json').write_text(json.dumps({
        'top_module': 'core', 'rtl_sources': ['rtl/core.v'], 'testbench_sources': [],
        'formal_equivalence': {'required': True, 'method': 'reset-state-relation',
                               'reset_start': {'signal': 'rst_n', 'active': 0}},
    }))
    return tmp_path


real_yosys = pytest.mark.skipif(find_yosys() is None, reason='Yosys integration tool not installed')


@pytest.mark.parametrize('reset', [None, {}, {'signal': 'rst_n'}, {'signal': 'rst_n', 'active': False},
                                  {'signal': 'rst_n', 'active': 2},
                                  {'signal': 'rst_n', 'active': 0, 'cycles': 1}])
def test_reset_start_must_be_explicit_and_exact(reset):
    with pytest.raises(ValueError):
        proof_method({'formal_equivalence': {'method': 'reset-state-relation', 'reset_start': reset}})


def test_reset_start_cannot_silently_weaken_another_method():
    with pytest.raises(ValueError):
        proof_method({'formal_equivalence': {'method': 'state-bijection', 'reset_start': {'signal': 'rst_n', 'active': 0}}})


@real_yosys
def test_merged_state_proves_only_under_declared_reset_contract(project):
    report = run_lec(project, netlist='candidate.v')
    assert report['status'] == 'pass', report['reason']
    relation = report['state_relation']
    assert (relation['state_bits'], relation['gate_state_bits'], relation['state_classes']) == (10, 8, 8)
    assert relation['transition_bits_checked'] == 18
    assert report['contract']['pre_reset_behavior'] == 'excluded'
    assert lec_valid(project, report)[0]
    assert 'after declared reset' in lec_valid(project, report)[1]
    # The stronger arbitrary-init contract remains nonpassing; no fallback.
    manifest = json.loads((project / 'project.json').read_text())
    manifest['formal_equivalence'] = {'method': 'state-bijection'}
    (project / 'project.json').write_text(json.dumps(manifest))
    assert run_lec(project, netlist='candidate.v')['status'] == 'fail'
    assert not lec_valid(project, report)[0]


@real_yosys
def test_every_gold_member_transition_is_checked_even_after_merge(project):
    # Omitting the extra golden state's D comparison would incorrectly pass.
    (project / 'rtl/core.v').write_text(GOLD.replace('low<=count[1:0]+1', 'low<=count[1:0]+2'))
    report = run_lec(project, netlist='candidate.v')
    assert report['status'] == 'fail'
    assert report['stages'][-1]['stage'] == 'state_proof', report['reason']
    assert (project / report['run_directory'] / 'counterexample.json').is_file()


@real_yosys
@pytest.mark.parametrize('source,reason', [
    (GOLD.replace('low<=0', 'low<=1'), 'Reset base fails'),
    ('''module core(input clk, rst_n, en, output [9:0] y);
reg [7:0] count; reg [1:0] low;
always @(posedge clk or negedge rst_n) if(!rst_n) count<=0; else if(en) count<=count+1;
always @(posedge clk) if(!rst_n) low<=0; else if(en) low<=count[1:0]+1;
assign y={count,low}; endmodule''', 'Reset base'),
])
def test_reset_base_must_cover_all_state_members(project, source, reason):
    (project / 'rtl/core.v').write_text(source)
    report = run_lec(project, netlist='candidate.v')
    assert report['status'] == 'fail'
    assert reason in report['reason']


@real_yosys
def test_wrong_declared_reset_and_unknown_signal_are_rejected(project):
    manifest = json.loads((project / 'project.json').read_text())
    for reset in ({'signal': 'rst_n', 'active': 1}, {'signal': 'missing', 'active': 0}):
        manifest['formal_equivalence']['reset_start'] = reset
        (project / 'project.json').write_text(json.dumps(manifest))
        report = run_lec(project, netlist='candidate.v')
        assert report['status'] == 'fail'
        assert 'reset' in report['reason'].lower()


@real_yosys
def test_different_pre_reset_initial_values_are_explicitly_out_of_scope(project):
    (project / 'rtl/core.v').write_text(GOLD.replace('reg [7:0] count;', "reg [7:0] count=8'h12;").replace(
        'reg [1:0] low;', "reg [1:0] low=2'b11;"))
    (project / 'candidate.v').write_text(GATE.replace('reg [7:0] count;', "reg [7:0] count=8'h55;"))
    report = run_lec(project, netlist='candidate.v')
    assert report['status'] == 'pass', report['reason']
    assert report['contract'] == {'start': 'after-declared-asynchronous-reset',
        'reset_start': {'signal': 'rst_n', 'active': 0}, 'pre_reset_behavior': 'excluded'}


@real_yosys
def test_scope_and_class_tampering_do_not_validate(project):
    from fpga_agent.evidence import sha
    report = run_lec(project, netlist='candidate.v')
    assert report['status'] == 'pass', report['reason']
    edited = copy.deepcopy(report)
    edited['contract']['pre_reset_behavior'] = 'proved'
    assert not lec_valid(project, edited)[0]
    run = project / report['run_directory']
    path = run / 'state_relation.json'
    data = json.loads(path.read_text())
    merged = next(group for group in data['classes'] if len(group['gold']) > 1)
    merged['gold'].pop()
    path.write_text(json.dumps(data))
    edited = copy.deepcopy(report)
    edited['artifacts'][path.relative_to(project).as_posix()] = sha(path)
    assert not lec_valid(project, edited)[0]


def test_missing_tool_cannot_accept_a_reset_assumption(project):
    with patch('fpga_agent.lec.find_yosys', return_value=None):
        assert run_lec(project)['status'] == 'blocked'
