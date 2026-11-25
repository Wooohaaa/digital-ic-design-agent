from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
from unittest.mock import patch
from zipfile import ZipFile

import pytest

from fpga_agent.autonomy import completion_gate
from fpga_agent.cli import do_package, do_td, main
from fpga_agent.lec import find_yosys, run_lec
from fpga_agent.quality import review_project
from fpga_agent.spec_gate import REPORT, specification_evidence
from fpga_agent.terminal import Settings, Workspace, make_plan
from fpga_agent.verify import verify_project


FIXTURE = Path(__file__).resolve().parents[1]/'examples/agent_validation/sync_fifo'


@pytest.fixture
def project(tmp_path):
    return shutil.copytree(FIXTURE, tmp_path/'sync_fifo',
                           ignore=shutil.ignore_patterns('build', 'reports', 'deliverables', '*_Runs'))


def test_project_contract_is_pinned_and_typed_action_uses_cli(project):
    workspace = Workspace(project.parent)
    plan = make_plan(workspace, Settings(), 'spec-check', {}, project)
    assert plan.commands[0][-2:] == ['spec-check', str(project)]
    assert specification_evidence(project).status == 'missing'
    reference = project/'spec/fifo_reference.sv'
    reference.write_bytes(reference.read_bytes()+b'\n// changed\n')
    state = specification_evidence(project)
    assert state.required and state.status == 'stale'
    assert 'reviewed hashes' in state.reason
    assert main(['spec-check', str(project)]) == 1
    assert not (project/REPORT).exists()


def test_reviewed_line_endings_do_not_allow_stale_run_inputs(project):
    from fpga_agent.evidence import simulation_input_hashes, trusted_testbench_valid
    from fpga_agent.project import load_manifest

    manifest = load_manifest(project)
    testbench = project/manifest['testbench_sources'][0]
    lf = testbench.read_bytes().replace(b'\r\n', b'\n')
    testbench.write_bytes(lf)
    before = simulation_input_hashes(project, manifest)
    testbench.write_bytes(lf.replace(b'\n', b'\r\n'))
    assert trusted_testbench_valid(project, manifest)[0]
    assert simulation_input_hashes(project, manifest) != before
    testbench.write_bytes(lf+b'\n// unreviewed edit\n')
    assert not trusted_testbench_valid(project, manifest)[0]


@pytest.mark.skipif(find_yosys() is None or not (shutil.which('iverilog') and shutil.which('vvp')),
                    reason='Yosys and Icarus required for real project gates')
def test_real_project_delivers_with_three_distinct_gates(project, tmp_path):
    workspace = Workspace(project.parent)
    task = {'project': project.name, 'target': 'simulation'}

    def gates():
        review = review_project(project, 'simulation', write=False)
        return review['artifact_ready'], {gate['id']: gate['status'] for gate in review['gates']}

    assert verify_project(project).status == 'pass'
    assert run_lec(project)['status'] == 'pass'
    ready, statuses = gates()
    assert not ready and statuses['DV-001'] == statuses['LEC-001'] == 'pass'
    assert statuses['FORMAL-001'] == 'missing'
    assert not completion_gate(workspace, task)[0]
    with patch('fpga_agent.cli.run_td', side_effect=AssertionError('TD should not be called')):
        assert do_td(project, 'synth', None) == 2
    assert do_package(project, True) == 2
    assert not (project/'deliverables').exists()
    with patch('fpga_agent.spec_gate.find_yosys', return_value=None):
        assert main(['spec-check', str(project)]) == 2
    assert specification_evidence(project).status == 'blocked'

    assert main(['flow', str(project), '--simulation-only']) == 0
    state = specification_evidence(project)
    assert state.status == 'current' and gates()[0]
    assert completion_gate(workspace, task)[0]
    archive_path, = (project/'deliverables').glob('*.zip')
    original_delivery = archive_path.read_bytes()
    restored = tmp_path/'restored'
    with ZipFile(archive_path) as archive:
        delivery = json.loads(archive.read('delivery.json'))
        assert delivery['specification_report_sha256'] == state.artifacts[REPORT]
        for name, digest in state.artifacts.items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == digest
        archive.extractall(restored)
    assert specification_evidence(restored) == state
    assert review_project(restored, 'simulation', write=False)['artifact_ready']
    witness = next(restored.glob('build/specification/*/*/reset_trace-witness.json'))
    witness.write_text('{}', encoding='utf-8')
    assert specification_evidence(restored).status == 'stale'
    assert not review_project(restored, 'simulation', write=False)['artifact_ready']

    # This faulty implementation still passes the trusted simulation (no write on reset)
    # and remains equivalent to its own synthesized netlist.
    rtl = project/'rtl/sync_fifo.v'
    source = rtl.read_bytes()
    assert source.count(b'if (!rst_n) begin') == 1
    rtl.write_bytes(source.replace(b'if (!rst_n) begin', b'if (!rst_n && !wr_en) begin'))
    assert specification_evidence(project).status == 'stale'
    assert verify_project(project).status == 'pass'
    assert run_lec(project)['status'] == 'pass'
    assert main(['spec-check', str(project)]) == 1
    ready, statuses = gates()
    assert not ready and statuses['DV-001'] == statuses['LEC-001'] == 'pass'
    assert statuses['FORMAL-001'] == 'fail'
    assert not completion_gate(workspace, task)[0]
    assert do_package(project, True) == 2
    assert archive_path.read_bytes() == original_delivery
    failure = json.loads((project/REPORT).read_text(encoding='utf-8'))
    witness = project/failure['run_directory']/'original/reset_base-witness.json'
    assert witness.is_file()


@pytest.mark.skipif(find_yosys() is None or not (shutil.which('iverilog') and shutil.which('vvp')),
                    reason='Yosys and Icarus required for real project gates')
def test_multisource_counter_binds_order_helper_and_parameters(tmp_path):
    source = FIXTURE.parents[1]/'lec/counter'
    project = shutil.copytree(source, tmp_path/'counter',
                             ignore=shutil.ignore_patterns('build', 'reports', 'deliverables'))
    assert main(['flow', str(project), '--simulation-only']) == 0
    state = specification_evidence(project)
    assert state.status == 'current'
    assert {'rtl/counter.sv', 'rtl/counter_demo.sv'} <= state.artifacts.keys()
    archive_path, = (project/'deliverables').glob('*.zip')
    with ZipFile(archive_path) as archive:
        archive.extractall(tmp_path/'relocated')
    assert specification_evidence(tmp_path/'relocated') == state
    assert review_project(tmp_path/'relocated', 'simulation', write=False)['artifact_ready']
    manifest_path = project/'project.json'
    original_manifest = manifest_path.read_bytes()
    manifest = json.loads(original_manifest)
    manifest['rtl_sources'].reverse()
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
    assert specification_evidence(project).status == 'stale'
    manifest_path.write_bytes(original_manifest)
    helper = project/'rtl/counter.sv'
    original = helper.read_bytes()
    helper.write_bytes(original+b'\n// helper edit\n')
    assert specification_evidence(project).status == 'stale'
    helper.write_bytes(original)
    wrapper = project/'rtl/counter_demo.sv'
    wrapper.write_bytes(wrapper.read_bytes().replace(b"RESET_ACTIVE = 1'b1", b"RESET_ACTIVE = 1'b0"))
    assert specification_evidence(project).status == 'stale'
    assert main(['spec-check', str(project)]) == 1
    failure = specification_evidence(project)
    assert failure.status == 'fail'
    assert 'parameter RESET_ACTIVE default does not match' in failure.reason


@pytest.mark.skipif(find_yosys() is None, reason='Yosys required to inspect the elaborated reference')
@pytest.mark.parametrize(('original', 'replacement', 'diagnostic'), [
    (b'counter_demo dut', b"counter_demo #(.RESET_ACTIVE(1'b0)) dut", 'using its checked defaults'),
    (b'.en(en)', b".en(1'b0)", 'input en'),
])
def test_reference_cannot_narrow_the_reviewed_dut(tmp_path, original, replacement, diagnostic):
    source = FIXTURE.parents[1]/'lec/counter'
    project = shutil.copytree(source, tmp_path/'counter',
                             ignore=shutil.ignore_patterns('build', 'reports', 'deliverables'))
    manifest_path = project/'project.json'
    manifest = json.loads(manifest_path.read_bytes())
    name = 'spec/counter_reference.sv'
    reference = project/name
    content = reference.read_bytes().replace(b'\r\n', b'\n')
    assert content.count(original) == 1
    content = content.replace(original, replacement)
    reference.write_bytes(content)
    manifest['formal_specification']['trusted_hashes'][name] = hashlib.sha256(content).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
    assert main(['spec-check', str(project)]) == 1
    failure = specification_evidence(project)
    assert failure.status == 'fail'
    assert diagnostic in failure.reason


@pytest.mark.skipif(find_yosys() is None or not (shutil.which('iverilog') and shutil.which('vvp')),
                    reason='Yosys and Icarus required for RTL and netlist APB acceptance')
def test_apb_wait_state_delivery_and_netlist_replay(tmp_path):
    source = FIXTURE.parent/'apb_wait_register'
    project = shutil.copytree(source, tmp_path/'apb',
                             ignore=shutil.ignore_patterns('build', 'reports', 'deliverables', '*_Runs'))
    assert main(['flow', str(project), '--simulation-only']) == 0
    state = specification_evidence(project)
    assert state.status == 'current'
    archive_path, = (project/'deliverables').glob('*.zip')
    delivery_bytes = archive_path.read_bytes()
    restored = tmp_path/'restored'
    with ZipFile(archive_path) as archive:
        archive.extractall(restored)
    assert specification_evidence(restored) == state
    assert review_project(restored, 'simulation', write=False)['artifact_ready']

    report = json.loads((restored/REPORT).read_bytes())
    proof_dir = restored/report['run_directory']
    contract = json.loads((restored/'spec/contract.json').read_bytes())
    for mutant in contract['mutations']:
        signals = json.loads((proof_dir/mutant/'reset_trace-witness.json').read_bytes())['signal']
        assert signals

    lec_report = json.loads((restored/'reports/lec.json').read_bytes())
    netlist = restored/lec_report['run_directory']/'netlist.v'
    assert lec_report['state_relation']['state_bits'] == 35
    replay = shutil.copytree(source, tmp_path/'replay',
                             ignore=shutil.ignore_patterns('build', 'reports', 'deliverables', '*_Runs'))
    (replay/'rtl/apb_wait_register.sv').write_bytes(netlist.read_bytes())
    assert verify_project(replay).status == 'pass'

    # Keep access high after completion: the mutant must not be released even
    # if synthesis faithfully preserves the faulty RTL.
    rtl = project/'rtl/apb_wait_register.sv'
    mutation = contract['mutations']['repeated_completion']
    original = rtl.read_bytes()
    anchor = mutation['before'].encode()
    assert original.count(anchor) == 1
    rtl.write_bytes(original.replace(anchor, mutation['after'].encode()))
    assert verify_project(project).status == 'fail'
    assert run_lec(project)['status'] == 'pass'
    assert main(['spec-check', str(project)]) == 1
    assert specification_evidence(project).status == 'fail'
    assert do_package(project, True) == 2
    assert archive_path.read_bytes() == delivery_bytes


@pytest.mark.skipif(find_yosys() is None, reason='Yosys required to inspect the elaborated DUT interface')
def test_reference_cannot_omit_an_input_from_the_contract(tmp_path):
    source = FIXTURE.parents[1]/'lec/counter'
    project = shutil.copytree(source, tmp_path/'counter',
                             ignore=shutil.ignore_patterns('build', 'reports', 'deliverables'))
    wrapper = project/'rtl/counter_demo.sv'
    original = wrapper.read_bytes()
    for current, updated in ((b'clk, rst, en,', b'clk, rst, en, inhibit,'),
                             (b'.en(en)', b'.en(en && !inhibit)')):
        assert original.count(current) == 1
        original = original.replace(current, updated)
    wrapper.write_bytes(original)

    manifest_path = project/'project.json'
    manifest = json.loads(manifest_path.read_bytes())
    name = 'spec/counter_reference.sv'
    reference = project/name
    content = reference.read_bytes().replace(b'\r\n', b'\n')
    assert content.count(b'.en(en)') == 1
    content = content.replace(b'.en(en)', b".en(en), .inhibit(1'b0)")
    reference.write_bytes(content)
    manifest['formal_specification']['trusted_hashes'][name] = hashlib.sha256(content).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
    assert main(['spec-check', str(project)]) == 1
    failure = specification_evidence(project)
    assert failure.status == 'fail'
    assert 'must cover every DUT input' in failure.reason
