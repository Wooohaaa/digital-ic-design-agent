"""Bind a passing FIFO simulation to synthesis, LEC and netlist replay."""
from __future__ import annotations

import argparse
import json
import re
import shutil
import uuid
from pathlib import Path

from fpga_agent.evidence import local_file, sha
from fpga_agent.lec import lec_valid, run_lec
from fpga_agent.project import load_manifest
from fpga_agent.sv_conformance import case_sources, conformance_valid, default_suite, load_contract, run_conformance


CASE = 'UVM-RTL-FIFO'
CASES = (CASE, 'UVM-FIFO-SYNC-RESET', 'UVM-FIFO-REPEAT-RESET', 'SV-FIFO-RESET-PRIORITY')


def run_flow(rtl_report: Path, output: Path, *, timeout: int = 180,
             uvm_root: Path | None = None, case_id: str = CASE) -> dict:
    rtl_report, output = rtl_report.resolve(), output.resolve()
    if rtl_report == output or timeout <= 0:
        raise ValueError('Distinct evidence paths and a positive timeout are required')
    suite = default_suite().resolve()
    rtl_report_hash = sha(rtl_report)
    valid, reason = conformance_valid(rtl_report, suite=suite)
    if not valid:
        raise ValueError(reason)
    simulation = json.loads(rtl_report.read_text(encoding='utf-8'))
    if case_id not in CASES or not any(row['id'] == case_id and row['status'] == 'pass'
                                      for row in simulation['cases']):
        raise ValueError(f'Passing {case_id} RTL evidence required')
    case = next(case for case in load_contract(suite)['cases'] if case['id'] == case_id)
    dut, _ = case_sources(suite, case)
    source = dut.relative_to(suite).as_posix()
    manifest_path = dut.parent/'project.json'
    manifest = load_manifest(dut.parent)
    if (manifest['top_module'] != 'fifo_two' or manifest['rtl_sources'] != [dut.name]
            or manifest['formal_equivalence'] != {'required': True, 'method': 'state-bijection'}):
        raise ValueError('FIFO LEC project differs from the simulation DUT or proof contract')
    digest = simulation['inputs'][source]
    if sha(dut) != digest:
        raise ValueError('Simulation DUT no longer matches the source')

    run = output.parent/'fifo-flows'/uuid.uuid4().hex[:12]
    project = run/'lec'
    project.mkdir(parents=True)
    for path in (dut, manifest_path):
        expected_digest = simulation['inputs'][path.relative_to(suite).as_posix()]
        target = project/path.name
        shutil.copyfile(path, target)
        if sha(target) != expected_digest:
            raise ValueError(f'Staged LEC input changed: {path.name}')
    proof = run_lec(project, timeout=timeout)
    valid_proof, proof_reason = lec_valid(project, proof)
    proof_report = project/'reports/lec.json'
    proof_dir = local_file(project, proof['run_directory']+'/lec.log').parent
    if not valid_proof or proof['inputs'][dut.name] != digest or sha(proof_dir/'inputs'/dut.name) != digest:
        raise ValueError(f'LEC did not verify the simulated RTL: {proof["reason"]}; {proof_reason}; {proof_report}')
    netlist = local_file(proof_dir, 'netlist.v')
    netlist_hash = sha(netlist)
    net_suite = run/'netlist-suite'
    shutil.copytree(suite, net_suite)
    shutil.copyfile(netlist, net_suite/source)
    replay_path = run/'netlist-sim.json'
    replay = run_conformance(replay_path, suite=net_suite, case_ids=[case_id],
                             backend=simulation['backend'], timeout=timeout, uvm_root=uvm_root)
    valid_replay, replay_reason = conformance_valid(replay_path, suite=net_suite)
    expected_inputs = {**simulation['inputs'], source: netlist_hash}
    if (not valid_replay or replay['inputs'] != expected_inputs
            or replay['cases'][0]['status'] != 'pass'):
        raise ValueError(f'Netlist simulation did not verify: {replay["cases"][0]["reason"]}; {replay_reason}; {replay_path}')

    # Flip ready in the synthesized Verilog while preserving the interface/state inventory.
    netlist_text = netlist.read_text(encoding='utf-8')
    changed, count = re.subn(r'(?m)^(\s*assign in_ready\s*=\s*)([^;]+);',
                             r'\g<1>!(\2);', netlist_text)
    if count != 1:
        raise ValueError('Expected one synthesized in_ready assignment for the counterexample probe')
    bad_project = run/'negative'
    shutil.copytree(project, bad_project, ignore=shutil.ignore_patterns('build', 'reports'))
    (bad_project/'wrong_ready.v').write_text(changed, encoding='utf-8')
    bad = run_lec(bad_project, netlist='wrong_ready.v', timeout=timeout)
    counterexample = bad['run_directory']+'/counterexample.json'
    bad_report = bad_project/'reports/lec.json'
    if (bad['status'] != 'fail' or [stage['stage'] for stage in bad['stages']]
            != ['version', 'normalize', 'gate_prepare', 'state_proof']
            or any(stage['returncode'] != 0 for stage in bad['stages'][:-1])
            or bad['stages'][-1]['returncode'] == 0
            or counterexample not in bad['artifacts']):
        raise ValueError(f'Expected a SAT counterexample for the mutated netlist: {bad_report}')
    witness = local_file(bad_project, counterexample)
    signals = json.loads(witness.read_text(encoding='utf-8'))['signal']
    if not any(signal['name'] == 'proof_ok' and signal['wave'].startswith('0') for signal in signals):
        raise ValueError('SAT witness does not show proof_ok=0')
    if sha(witness) != bad['artifacts'][counterexample] or sha(rtl_report) != rtl_report_hash:
        raise ValueError('Verification evidence changed during execution')

    def record(path: Path) -> dict:
        return {'path': str(path), 'sha256': sha(path)}

    relation = proof['state_relation']
    summary = {'schema_version': 1, 'status': 'pass', 'case': case_id, 'runner_sha256': sha(Path(__file__)),
               'scope': 'Single-clock generic synthesis, state-bijection LEC and bounded RTL/netlist simulation',
               'profile': simulation['profile'], 'backend': simulation['backend'],
               'rtl_source': {'suite_path': source, **record(dut)}, 'rtl_simulation': record(rtl_report),
               'lec': {**record(proof_report), 'contract': proof['contract'], 'tool_version': proof['tool_version'],
                       'state_bits': relation['state_bits'], 'output_bits': relation['output_bits']},
               'netlist': record(netlist), 'netlist_simulation': record(replay_path),
               'negative_lec': {**record(bad_report), 'counterexample': record(witness), 'reason': bad['reason']}}
    output.write_text(json.dumps(summary, indent=2)+'\n', encoding='utf-8')
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rtl-report', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--uvm-root', type=Path)
    parser.add_argument('--case', choices=CASES, default=CASE)
    parser.add_argument('--timeout', type=int, default=180)
    args = parser.parse_args()
    try:
        run_flow(args.rtl_report, args.output, timeout=args.timeout, uvm_root=args.uvm_root, case_id=args.case)
    except (OSError, ValueError) as exc:
        parser.exit(1, f'FIFO flow failed: {exc}\n')
    print(f'FIFO RTL, LEC, netlist replay and counterexample verified: {args.output}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
