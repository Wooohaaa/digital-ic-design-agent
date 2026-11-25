"""Reproduce the RV32I Stage 2 acceptance gates from the repository root."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import shutil

from .cpu_act import ASSETS, CASES, run_act_matrix
from .cpu_agent import design_cpu
from .cpu_bench import prepare_project, run_benchmark
from .cpu_eei import fault_cases, run_fault_matrix
from .cpu_external import CORES, _write_json, run_matrix
from .evidence import sha, sha_lf
from .lec import lec_valid, run_lec


INSTRUCTIONS = set('ADD ADDI AND ANDI AUIPC BEQ BGE BGEU BLT BLTU BNE EBREAK FENCE JAL JALR '
                   'LB LBU LH LHU LUI LW OR ORI SB SH SLL SLLI SLT SLTI SLTIU SLTU '
                   'SRA SRAI SRL SRLI SUB SW XOR XORI'.split())
BRANCH_OUTCOMES = {f'{op}:{outcome}' for op in ('BEQ', 'BNE', 'BGE', 'BGEU', 'BLT', 'BLTU')
                   for outcome in ('taken', 'not_taken')}
MEMORY_LANES = {f'{op}:lane{lane}' for ops, step in ((('LB', 'LBU', 'SB'), 1),
                                                    (('LH', 'LHU', 'SH'), 2), (('LW', 'SW'), 4))
                for op in ops for lane in range(0, 4, step)}
REQUIRED_COVERAGE = INSTRUCTIONS | BRANCH_OUTCOMES | MEMORY_LANES | {'trap'}
IMPLEMENTATION = 'rtl/rv32_core.v'


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _receipt(output: Path, report: Path) -> dict:
    return {'path': report.relative_to(output).as_posix(), 'sha256': sha(report)}


def _replay(root: Path, output: Path, stage: dict) -> dict[str, Path]:
    designs = {}
    for core in CORES:
        source = root/f'benchmarks/rv32_{core}'
        target = output/'replay-source'/'benchmarks'/source.name
        response = source/'generation/luna-response.json'
        result = design_cpu(target, core, provider='exchange', model='gpt-6-luna',
                            rounds=0, seeds=64, response_file=response)
        _require(result['status'] == 'pass' and result['board_soc'] == 'pass'
                 and result['case_count'] == 114, f'{core}: benchmark or board SoC signature failed')
        _require(not REQUIRED_COVERAGE - set(result['instruction_coverage']),
                 f'{core}: instruction, branch-outcome or byte-lane coverage gap')
        rtl = target/IMPLEMENTATION
        _require(sha_lf(rtl) == sha_lf(source/IMPLEMENTATION),
                 f'{core}: accepted exchange response did not reproduce the project RTL')
        manifest = target/'project.json'
        config = json.loads(manifest.read_bytes())
        config['formal_equivalence'] = json.loads((source/'project.json').read_bytes())['formal_equivalence']
        _require(config['formal_equivalence'].get('target_top') == 'rv32_core', 'Expected a core LEC contract')
        _write_json(manifest, config)
        stage[core] = {'response_sha256': sha(response), 'model': 'gpt-6-luna',
                       'transport': 'saved exchange replay', 'rtl_sha256': sha(rtl),
                       'corpus': _receipt(output, target/'reports/simulation.json'),
                       'coverage': sorted(REQUIRED_COVERAGE), 'board_soc': 'pass'}
        designs[core] = rtl
    return designs


def _synthesize(output: Path, designs: dict[str, Path], stage: dict) -> dict[str, Path]:
    netlists = {}
    for core, rtl in designs.items():
        project = rtl.parent.parent
        result = run_lec(project, timeout=180)
        valid, reason = lec_valid(project, result)
        _require(valid, f'{core}: LEC rejected: {reason}')
        directory = project/result['run_directory']
        netlist = directory/'netlist.v'
        gate = json.loads((directory/'gate.json').read_bytes())['modules']['rv32_core']
        stage[core] = {'method': result['method'], 'contract': result['contract'],
                       'state_bits': result['state_relation']['state_bits'],
                       'generic_cells': dict(sorted(Counter(cell['type'] for cell in gate['cells'].values()).items())),
                       'netlist': _receipt(output, netlist),
                       'proof': _receipt(output, project/'reports/lec.json')}
        target = output/'netlist-source'/f'benchmarks/rv32_{core}'/IMPLEMENTATION
        target.parent.mkdir(parents=True)
        shutil.copyfile(netlist, target)
        netlists[core] = target
    return netlists


def _negative_controls(root: Path, output: Path, rtl: Path, summary: dict) -> None:
    archived = root/'benchmarks/failures/rv32_multi_initial'/IMPLEMENTATION
    project = output/'known-failing-multi'
    (project/'rtl').mkdir(parents=True)
    shutil.copyfile(archived, project/IMPLEMENTATION)
    prepare_project(project)
    failed = run_benchmark(project, seeds=0)
    _require(failed['status'] == 'fail' and failed['cases']
             and 'retirement mismatch' in failed['cases'][-1].get('error', ''),
             'Archived multi-cycle fault was not detected by the trusted benchmark')
    summary['historical_retirement_fault'] = _receipt(output, project/'reports/simulation.json')

    source = rtl.read_bytes()
    anchor = b'&& legal && !misaligned && (is_load || is_store)'
    _require(source.count(anchor) == 1, 'Expected one single-cycle memory-valid mutation anchor')
    mutant = output/'mutant.v'
    mutant.write_bytes(source.replace(anchor, b'&& legal && (is_load || is_store)'))
    faults = run_fault_matrix({'mutant': mutant}, output/'fault-negative-control')
    results = faults['implementations']['mutant']['cases']
    row = results['sw_lane2']
    _require(faults['status'] == 'fail' and 'stores mismatch' in row.get('error', ''),
             'Fault suite failed to detect a write escaping a trapping store')
    summary['escaping_store'] = {'case': 'sw_lane2', 'reason': row['error'],
                                 **_receipt(output, output/'fault-negative-control/report.json')}


def run_stage2(root: Path, suite: Path, clang: str, output: Path, seeds: int = 256) -> dict:
    root, output = root.resolve(), output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = {'status': 'fail', 'runner_sha256': sha(Path(__file__)), 'seeds': seeds, 'gates': {},
              'scope': 'RV32I 2.1 instruction and terminal-exception core EEI; generic netlist',
              'deferred': {
                  'ACT4-platform': 'Historical upstream ACT RV32I directory executed; ACT4 platform and certification outside this run',
                  'fresh-live-generation': 'Uses archived model responses; no new provider request is part of replay',
              }}
    gates = report['gates']
    try:
        gates['generation_replay'] = {}
        designs = _replay(root, output, gates['generation_replay'])
        gates['netlist_lec'] = {}
        netlists = _synthesize(output, designs, gates['netlist_lec'])
        sources = {**designs, **{f'{core}-netlist': source for core, source in netlists.items()}}
        faults = run_fault_matrix(sources, output/'terminal-exceptions')
        _require(faults['status'] == 'pass', 'Terminal exception matrix failed')
        gates['terminal_exceptions'] = {'cases_per_implementation': len(fault_cases()),
                                        **_receipt(output, output/'terminal-exceptions/report.json')}

        corpus = run_matrix(output/'replay-source', output/'external-rtl', seeds)
        _require(corpus['status'] == 'pass', 'External retirement and memory differential failed')
        gates['external_differential'] = _receipt(output, output/'external-rtl/report.json')
        report['benchmark'] = {
            core: {
                'programs': len(rows), 'retired': sum(row['retired'] for row in rows),
                'stores': sum(row['stores'] for row in rows), 'cycles': sum(row['cycles'] for row in rows),
            } for core, rows in corpus['cores'].items()
        }
        locked = json.loads((ASSETS/'suite.json').read_bytes())
        cases = tuple(Path(name).stem for name in locked['files'] if name.startswith(CASES+'/'))
        tests = {}
        for label, sources in (('rtl', output/'replay-source'), ('netlist', output/'netlist-source')):
            directory = output/f'act-{label}'
            result = run_act_matrix(sources, suite, directory, clang, cases)
            _require(result['status'] == 'pass', f'ACT {label} signature matrix failed')
            tests[label] = {**_receipt(output, directory/'report.json'), 'cases_per_core': len(cases)}
        for case in cases:
            for name in ('image.hex', 'reference.json'):
                _require(sha(output/'act-rtl/cases'/case/name) == sha(output/'act-netlist/cases'/case/name),
                         f'{case}: RTL and netlist tests received different {name}')
        gates['act_signatures'] = tests
        gates['negative_controls'] = {}
        _negative_controls(root, output, designs['single'], gates['negative_controls'])
        report['status'] = 'pass'
    except (ValueError, OSError, RuntimeError) as exc:
        report['error'] = str(exc)
    _write_json(output/'report.json', report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--act-suite', type=Path, required=True)
    parser.add_argument('--clang', default='clang')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seeds', type=int, default=256)
    args = parser.parse_args()
    if not 64 <= args.seeds <= 1024:
        parser.error('--seeds must be between 64 and 1024')
    report = run_stage2(Path.cwd(), args.act_suite, args.clang, args.output, args.seeds)
    print(f"Stage 2 acceptance: {report['status']}; report: {args.output/'report.json'}")
    return int(report['status'] != 'pass')


if __name__ == '__main__':
    raise SystemExit(main())
