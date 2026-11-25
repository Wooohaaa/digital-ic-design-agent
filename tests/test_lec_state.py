"""Positive and adversarial tests of the complete state relation, using real SAT."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from fpga_agent.lec import find_yosys, lec_valid, run_lec
from fpga_agent.lec_state import build_state_miter


COUNTER = '''module core(input clk, rst, en, output [7:0] y);
reg [7:0] count;
always @(posedge clk) if (rst) count <= 0; else if (en) count <= count + 1;
assign y=count;
endmodule
'''


@pytest.fixture
def project(tmp_path):
    (tmp_path / 'rtl').mkdir()
    (tmp_path / 'rtl/core.sv').write_text(COUNTER)
    (tmp_path / 'project.json').write_text(json.dumps({
        'top_module': 'core', 'rtl_sources': ['rtl/core.sv'], 'testbench_sources': [],
        'formal_equivalence': {'required': True, 'method': 'state-bijection'},
    }))
    return tmp_path


real_yosys = pytest.mark.skipif(find_yosys() is None, reason='Yosys integration tool not installed')


@real_yosys
def test_complete_relation_and_rebuilt_miter(project):
    report = run_lec(project)
    assert report['status'] == 'pass', report['reason']
    assert report['state_relation']['state_bits'] == 8
    assert report['state_relation']['output_bits'] == 8
    assert lec_valid(project, report)[0]
    run = project / report['run_directory']
    miter, relation = build_state_miter(run / 'gold.json', run / 'gate.json', 'core')
    assert len(relation['mapping']) == 8
    assert all(c['type'] not in {'$dff', '$adff'} for c in miter['modules']['state_miter']['cells'].values())
    assert all(m['init'] == 'x' for m in relation['mapping'])
    # Even updated artifact digests cannot excuse an omitted mapping/miter bit.
    from fpga_agent.evidence import sha
    for filename in ('state_relation.json', 'state_miter.json'):
        path = run / filename
        original = path.read_bytes()
        data = json.loads(original)
        if filename == 'state_relation.json':
            data['mapping'].pop()
        else:
            data['modules']['state_miter']['cells'].pop('all_obligations')
        path.write_text(json.dumps(data))
        edited = copy.deepcopy(report)
        edited['artifacts'][path.relative_to(project).as_posix()] = sha(path)
        assert not lec_valid(project, edited)[0]
        path.write_bytes(original)


@real_yosys
@pytest.mark.parametrize('before,after', [
    ('count <= 0', 'count <= 1'),
    ('count + 1', 'count + 2'),
    ('assign y=count', "assign y=count ^ 8'h80"),
    ('if (rst)', 'if (!rst)'),
    ('if (en)', 'if (!en)'),
])
def test_wrong_reset_transition_output_and_enable_have_counterexamples(project, before, after):
    (project / 'candidate.v').write_text(COUNTER.replace(before, after))
    report = run_lec(project, netlist='candidate.v')
    assert report['status'] == 'fail'
    assert report['stages'][-1]['stage'] == 'state_proof', report['reason']
    assert report['stages'][-1]['returncode'] != 0
    assert (project / report['run_directory'] / 'counterexample.json').is_file()


@real_yosys
def test_initialized_register_sets_match_and_different_initial_value_is_rejected(project):
    initialized = COUNTER.replace('reg [7:0] count;', "reg [7:0] count = 8'h35;")
    (project / 'rtl/core.sv').write_text(initialized)
    report = run_lec(project)
    assert report['status'] == 'pass', report['reason']
    assert any(m['init'] == '1' for m in report['state_relation']['mapping'])
    (project / 'candidate.v').write_text(initialized.replace("8'h35", "8'h34"))
    bad = run_lec(project, netlist='candidate.v')
    assert bad['status'] == 'fail'
    assert 'Initial-state sets differ' in bad['reason']
    (project / 'candidate.v').write_text(COUNTER)
    assert 'Initial-state sets differ' in run_lec(project, netlist='candidate.v')['reason']


@real_yosys
def test_async_reset_positive_and_event_mutations(project):
    async_core = COUNTER.replace('posedge clk)', 'posedge clk or posedge rst)')
    (project / 'rtl/core.sv').write_text(async_core)
    report = run_lec(project)
    assert report['status'] == 'pass', report['reason']
    assert all(m['async_reset'] == ['rst', 0, 1, '0'] for m in report['state_relation']['mapping'])
    for candidate in (async_core.replace('count <= 0', 'count <= 1'), COUNTER,
                      async_core.replace('posedge rst', 'negedge rst').replace('if (rst)', 'if (!rst)')):
        (project / 'candidate.v').write_text(candidate)
        bad = run_lec(project, netlist='candidate.v')
        assert bad['status'] == 'fail'
        assert 'reset event' in bad['reason'], bad['reason']


@real_yosys
def test_multiple_clocks_and_latches_fail_closed(project):
    (project / 'rtl/core.sv').write_text('''module core(input clk, clk2, rst, output [1:0] y);
reg a,b; always @(posedge clk) a<=rst; always @(posedge clk2) b<=!rst;
assign y={a,b}; endmodule''')
    report = run_lec(project)
    assert report['status'] == 'fail'
    assert 'Multiple clocks' in report['reason']
    (project / 'rtl/core.sv').write_text('''module core(input en,a, output reg q);
always @* if(en) q=a; endmodule''')
    report = run_lec(project)
    assert report['status'] == 'fail'
    assert 'Unsupported state/opaque cell' in report['reason']


@real_yosys
def test_incomplete_state_correspondence_is_rejected(project):
    # Output aliases can legitimately match renamed state. Changing the output
    # logic removes that alias, so names cannot smuggle a partial relation.
    (project / 'candidate.v').write_text(COUNTER.replace('count', 'other_state').replace(
        'assign y=other_state;', 'assign y=other_state+1;'))
    report = run_lec(project, netlist='candidate.v')
    assert report['status'] == 'fail'
    assert 'Incomplete state bijection' in report['reason']


@real_yosys
def test_fifo_memory_and_pipeline_state_preserved():
    project = Path(__file__).resolve().parents[1] / 'examples/agent_validation/sync_fifo'
    report = run_lec(project)
    assert report['status'] == 'pass', report['reason']
    assert report['state_relation']['state_bits'] == 47
    assert report['state_relation']['output_bits'] == 10
    assert lec_valid(project, report)[0]


@real_yosys
def test_initialized_ram_and_external_candidate_use_snapshot_images(project):
    ram = '''module core(input clk, we, addr, input [7:0] din, output [7:0] y);
reg [7:0] mem[0:1];
initial $readmemh("image.hex",mem);
always @(posedge clk) if(we) mem[addr]<=din;
assign y=mem[addr]; endmodule'''
    (project / 'rtl/core.sv').write_text(ram)
    (project / 'image.hex').write_text('12\n34\n')
    report = run_lec(project)
    assert report['status'] == 'pass', report['reason']
    assert report['state_relation']['state_bits'] == 16
    assert lec_valid(project, report)[0]
    (project / 'candidate.v').write_text(ram)
    assert run_lec(project, netlist='candidate.v')['status'] == 'pass'
    (project / 'wrong.hex').write_text('12\n35\n')
    (project / 'candidate.v').write_text(ram.replace('image.hex', 'wrong.hex'))
    bad = run_lec(project, netlist='candidate.v')
    assert bad['status'] == 'fail'
    assert 'Initial-state sets differ' in bad['reason']
    (project / 'image.hex').write_text('00\n00\n')
    assert not lec_valid(project, report)[0]
