from __future__ import annotations

import copy
import json
import subprocess
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from fpga_agent.lec import find_yosys, lec_inputs, lec_valid, run_lec
from fpga_agent.quality import review_project


@pytest.fixture
def project(tmp_path):
    (tmp_path / 'rtl').mkdir()
    (tmp_path / 'rtl/core.sv').write_text(
        'module core(input [7:0] a, b, output [7:0] y); assign y = a + b; endmodule\n')
    (tmp_path / 'project.json').write_text(json.dumps({
        'top_module': 'core', 'rtl_sources': ['rtl/core.sv'], 'testbench_sources': [],
        'formal_equivalence': {'required': True},
    }))
    return tmp_path


def test_missing_tool_is_blocked_and_replaces_previous_report(project):
    (project / 'reports').mkdir()
    (project / 'reports/lec.json').write_text('{"status":"pass"}')
    with patch('fpga_agent.lec.find_yosys', return_value=None):
        report = run_lec(project)
    assert report['status'] == 'blocked'
    assert json.loads((project / 'reports/lec.json').read_text())['status'] == 'blocked'
    assert not lec_valid(project, report)[0]


def test_timeout_never_passes(project):
    with patch('fpga_agent.lec.subprocess.run', side_effect=subprocess.TimeoutExpired('yosys', 1)):
        assert run_lec(project, yosys='yosys', timeout=1)['status'] == 'fail'


def test_unsafe_paths_and_dynamic_includes_rejected(project):
    with pytest.raises(ValueError):
        lec_inputs(project, '../outside.v')
    (project / 'rtl/core.sv').write_text('`include HEADER\n')
    with pytest.raises(ValueError, match='non-literal include'):
        lec_inputs(project)


def test_required_lec_blocks_otherwise_ready_review(project):
    with patch('fpga_agent.quality.simulation_valid', return_value=(True, 'pass')):
        report = review_project(project, 'simulation', write=False)
    assert not report['artifact_ready']
    assert next(g for g in report['gates'] if g['id'] == 'LEC-001')['status'] == 'blocked'


def test_agent_can_plan_lec_but_cannot_finish_without_required_proof(project):
    from fpga_agent.autonomy import completion_gate
    from fpga_agent.terminal import Settings, Workspace, make_plan
    workspace = Workspace(project)
    plan = make_plan(workspace, Settings(), 'lec', {}, project)
    assert plan.commands[0][-2:] == ['lec', str(project)]
    with patch('fpga_agent.autonomy.evidence', return_value={
        'simulation': 'current', 'lec_required': True, 'lec': 'missing',
    }):
        assert not completion_gate(workspace, {'project': '.', 'target': 'simulation'})[0]


real_yosys = pytest.mark.skipif(find_yosys() is None, reason='Yosys integration tool not installed')


@real_yosys
def test_real_synthesis_proof_staleness_and_mutation(project):
    report = run_lec(project)
    assert report['status'] == 'pass', report['reason']
    assert report['proven_cells'] > 0
    assert lec_valid(project, report)[0]
    assert not lec_valid(project, {'status': 'pass'})[0]
    incomplete = copy.deepcopy(report)
    incomplete['artifacts'] = {}
    assert not lec_valid(project, incomplete)[0]
    proof = project / report['run_directory'] / 'proof.json'
    old = proof.read_bytes()
    proof.write_bytes(old + b' ')
    assert not lec_valid(project, report)[0]
    proof.write_bytes(old)
    source = project / 'rtl/core.sv'
    source.write_text(source.read_text().replace('a + b', 'a - b'))
    assert not lec_valid(project, report)[0]
    # The old synthesized adder must fail against the now-subtracting RTL.
    changed = run_lec(project, netlist=report['run_directory'] + '/netlist.v')
    assert changed['status'] == 'fail'
    assert not lec_valid(project, changed)[0]


@real_yosys
def test_real_combinational_include_invalidation(project):
    (project / 'rtl/width.vh').write_text('`define WIDTH 8\n')
    (project / 'rtl/core.sv').write_text('''`include "width.vh"
module core(input [`WIDTH-1:0] a, b, output [`WIDTH-1:0] q);
assign q = a ^ b;
endmodule
''')
    report = run_lec(project)
    assert report['status'] == 'pass', report['reason']
    assert lec_valid(project, report)[0]
    (project / 'rtl/width.vh').write_text('`define WIDTH 9\n')
    assert not lec_valid(project, report)[0]


@real_yosys
def test_sequential_unproven_remains_nonpassing(project):
    (project / 'rtl/core.sv').write_text('''module core(input clk, rst, en, output reg [7:0] q);
always @(posedge clk) if (rst) q <= 0; else if (en) q <= q + 1;
endmodule''')
    report = run_lec(project)
    assert report['status'] == 'fail'
    assert not lec_valid(project, report)[0]


@real_yosys
def test_real_wrong_sequential_netlist_rejected(project):
    (project / 'rtl/core.sv').write_text('''module core(input clk, rst, output reg [7:0] q);
always @(posedge clk) if (rst) q <= 0; else q <= q + 1;
endmodule''')
    (project / 'wrong.v').write_text('''module core(input clk, rst, output reg [7:0] q);
always @(posedge clk) if (rst) q <= 0; else q <= q + 2;
endmodule''')
    assert run_lec(project, netlist='wrong.v')['status'] == 'fail'


@real_yosys
def test_real_invalid_rtl_and_vacuous_design_rejected(project):
    (project / 'rtl/core.sv').write_text('module core( syntax error endmodule')
    assert run_lec(project)['status'] == 'fail'
    (project / 'rtl/core.sv').write_text('module core(input a); endmodule')
    assert run_lec(project)['status'] == 'fail'


@real_yosys
@pytest.mark.parametrize('method', ['direct', 'state-bijection', 'reset-state-relation'])
def test_packaged_proof_restores_and_tampering_is_rejected(project, method):
    from fpga_agent.cli import do_package
    from fpga_agent.project import create_project
    # Reuse the board/spec scaffolding, while verifying this test's small adder.
    delivery = create_project('delivery', project / 'delivery')
    manifest = json.loads((delivery / 'project.json').read_text())
    manifest.update(top_module='core', rtl_sources=['rtl/core.sv'], testbench_sources=[],
                    formal_equivalence={'required': True, 'method': method})
    if method == 'reset-state-relation':
        manifest['formal_equivalence']['reset_start'] = {'signal': 'rst', 'active': 1}
    (delivery / 'project.json').write_text(json.dumps(manifest))
    (delivery / 'rtl/core.sv').write_bytes((project / 'rtl/core.sv').read_bytes())
    if method == 'state-bijection':
        (delivery / 'rtl/core.sv').write_text('''module core(input clk, rst, output reg [7:0] q);
always @(posedge clk) if(rst) q<=0; else q<=q+1; endmodule''')
    elif method == 'reset-state-relation':
        (delivery / 'rtl/core.sv').write_text('''module core(input clk, rst, output reg [7:0] q);
always @(posedge clk or posedge rst) if(rst) q<=0; else q<=q+1; endmodule''')
    report = run_lec(delivery)
    assert report['status'] == 'pass', report['reason']
    # This test isolates delivery from simulation; real simulation is separately exercised.
    (delivery / 'reports/simulation.json').write_text('{}')
    with patch('fpga_agent.cli.simulation_valid', return_value=(True, 'test stub')), \
         patch('fpga_agent.quality.simulation_valid', return_value=(True, 'test stub')):
        assert do_package(delivery, simulation_only=True) == 0
    restored = project / 'restored'
    with zipfile.ZipFile(delivery / 'deliverables/delivery-simulation.zip') as archive:
        archive.extractall(restored)
    restored_report = json.loads((restored / 'reports/lec.json').read_text())
    assert lec_valid(restored, restored_report)[0]
    netlist = delivery / report['run_directory'] / 'netlist.v'
    netlist.write_text(netlist.read_text() + '\n// edited\n')
    with patch('fpga_agent.cli.simulation_valid', return_value=(True, 'test stub')), \
         patch('fpga_agent.quality.simulation_valid', return_value=(True, 'test stub')):
        with pytest.raises(ValueError, match='Engineering review rejected'):
            do_package(delivery, simulation_only=True)
