import json
import subprocess
from unittest.mock import patch

import pytest

from fpga_agent.cli import main
from fpga_agent.lec import find_yosys, lec_inputs, lec_valid, run_lec


@pytest.fixture
def project(tmp_path):
    (tmp_path / 'core.sv').write_text(
        'module core(input [7:0] a, b, output [7:0] y); assign y = a + b; endmodule\n')
    (tmp_path / 'project.json').write_text(json.dumps({'top_module': 'core', 'rtl_sources': ['core.sv']}))
    return tmp_path


real_yosys = pytest.mark.skipif(find_yosys() is None, reason='Yosys integration tool not installed')


def test_missing_tool_replaces_previous_report(project):
    (project / 'reports').mkdir()
    (project / 'reports/lec.json').write_text('{"status":"pass"}')
    with patch('fpga_agent.lec.find_yosys', return_value=None):
        assert main(['lec', str(project)]) == 2
    assert json.loads((project / 'reports/lec.json').read_text())['status'] == 'blocked'


def test_timeout_never_passes(project):
    with patch('fpga_agent.lec.subprocess.run', side_effect=subprocess.TimeoutExpired('yosys', 1)):
        assert run_lec(project, yosys='yosys', timeout=1)['status'] == 'fail'


def test_unsafe_paths_and_dynamic_includes_rejected(project):
    with pytest.raises(ValueError):
        lec_inputs(project, '../outside.v')
    (project / 'core.sv').write_text('`include HEADER\n')
    with pytest.raises(ValueError, match='non-literal include'):
        lec_inputs(project)


@real_yosys
def test_synthesis_passes_and_wrong_netlist_produces_counterexample(project):
    assert main(['lec', str(project)]) == 0
    report_path = project / 'reports/lec.json'
    proof = json.loads(report_path.read_text())
    assert proof['output_bits'] == 8 and lec_valid(project, proof)[0]
    netlist = proof['run_directory'] + '/netlist.v'
    assert run_lec(project, netlist=netlist)['status'] == 'pass'
    source = project / 'core.sv'
    source.write_text(source.read_text().replace('a + b', 'a - b'))
    assert not lec_valid(project, proof)[0]
    assert main(['lec', str(project), '--netlist', netlist]) == 1
    failed = json.loads(report_path.read_text())
    assert failed['status'] == 'fail'
    assert failed['stages'][-1]['stage'] == 'proof'
    counterexample = project / failed['run_directory'] / 'counterexample.json'
    assert json.loads(counterexample.read_text())['signal']


@real_yosys
def test_include_change_and_missing_or_tampered_artifacts_invalidate_proof(project):
    include = project / 'width.vh'
    include.write_text('`define WIDTH 8\n')
    (project / 'core.sv').write_text(
        '`include "width.vh"\nmodule core(input [`WIDTH-1:0] a, output [`WIDTH-1:0] y); '
        'assign y = ~a; endmodule\n')
    proof = run_lec(project)
    assert proof['status'] == 'pass', proof['reason']
    assert lec_valid(project, proof)[0]
    assert not lec_valid(project, {**proof, 'artifacts': {}})[0]
    transcript = project / proof['run_directory'] / 'proof.log'
    original = transcript.read_bytes()
    transcript.write_bytes(original + b'\n')
    assert not lec_valid(project, proof)[0]
    transcript.write_bytes(original)
    include.write_text('`define WIDTH 9\n')
    assert not lec_valid(project, proof)[0]


@real_yosys
@pytest.mark.parametrize(('rtl', 'reason'), [
    ('module core(input clk, d, output reg q); always @(posedge clk) q <= d; endmodule',
     'combinational generic gates'),
    ('module core(input a); endmodule', 'No observable output ports'),
    ("module core(output q); assign q = 1'bx; endmodule", 'X or Z'),
])
def test_unsupported_designs_cannot_pass(project, rtl, reason):
    (project / 'core.sv').write_text(rtl)
    report = run_lec(project)
    assert report['status'] == 'fail'
    assert reason in report['reason']


@real_yosys
def test_candidate_cannot_omit_an_output(project):
    (project / 'candidate.v').write_text(
        'module core(input [7:0] a, b, output [6:0] y); assign y = a + b; endmodule')
    report = run_lec(project, netlist='candidate.v')
    assert report['status'] == 'fail'
    assert 'port interfaces differ' in report['reason']
