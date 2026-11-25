"""Reproduce the scoped LEC baseline without modifying tracked CPU manifests."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import uuid
from pathlib import Path

from fpga_agent.lec import run_lec


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cpu-cores', action='store_true', help='Also assess all three CPU cores; unproven cores make the matrix fail')
    parser.add_argument('--cpu', action='append', choices=('single', 'multi', 'pipeline'), default=[],
                        help='Assess selected CPU core(s); repeat to select more than one')
    parser.add_argument('--reset-cpu', action='append', choices=('single', 'multi', 'pipeline'), default=[],
                        help='Explicitly assess a CPU only after assertion of its active-low rst_n asynchronous reset')
    parser.add_argument('--project-cpus', action='store_true',
                        help='Use actual CPU project manifests and write their required core-proof evidence')
    parser.add_argument('--output', default='reports/lec-matrix.json')
    parser.add_argument('--timeout', type=int, default=180, help='Per-design wall-clock budget')
    args = parser.parse_args()
    if args.project_cpus and args.reset_cpu:
        parser.error('--project-cpus uses declared manifest contracts; do not combine it with --reset-cpu')
    root = Path(__file__).resolve().parents[1]
    projects = [('adder', root / 'examples/lec/adder'), ('counter', root / 'examples/lec/counter'),
                ('merged_counter', root / 'examples/lec/merged_counter'),
                ('sync_fifo', root / 'examples/agent_validation/sync_fifo'),
                ('apb_wait_register', root / 'examples/agent_validation/apb_wait_register')]
    sources = {}
    run_root = root / 'build/lec-matrix' / uuid.uuid4().hex
    architectures = ('single', 'multi', 'pipeline') if args.cpu_cores else list(dict.fromkeys(args.cpu + args.reset_cpu))
    if architectures:
        for architecture in architectures:
            source = root / f'benchmarks/rv32_{architecture}/rtl/rv32_core.v'
            if args.project_cpus:
                target = source.parent.parent
            else:
                target = run_root / f'rv32_{architecture}'
                (target / 'rtl').mkdir(parents=True)
                shutil.copyfile(source, target / 'rtl/rv32_core.v')
                formal = {'required': True, 'method': 'state-bijection'}
                if architecture in args.reset_cpu:
                    formal.update(method='reset-state-relation', reset_start={'signal': 'rst_n', 'active': 0})
                (target / 'project.json').write_text(json.dumps({
                    'top_module': 'rv32_core', 'rtl_sources': ['rtl/rv32_core.v'],
                    'formal_equivalence': formal,
                }, indent=2)+'\n', encoding='utf-8')
            name = f'rv32_{architecture}'
            projects.append((name, target))
            sources[name] = {'path': source.relative_to(root).as_posix(),
                             'sha256': hashlib.sha256(source.read_bytes()).hexdigest()}
    results = []
    for name, project in projects:
        report = run_lec(project, timeout=args.timeout)
        # Save this exact result beside the immutable run, even if a later run
        # replaces reports/lec.json. Baseline summaries can point at this copy.
        record = project / report['run_directory'] / 'result.json'
        record.write_text(json.dumps(report, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')
        relation = report.get('state_relation', {})
        item = {'name': name, 'status': report['status'], 'reason': report['reason'],
                'method': report['method'], 'contract': report['contract'],
                'proof_top': report['proof_top'], 'design_top': report['design_top'],
                'state_bits': relation.get('state_bits', 0), 'gate_state_bits': relation.get('gate_state_bits'),
                'state_classes': relation.get('state_classes'),
                'output_bits': relation.get('output_bits'), 'elapsed_seconds': report['elapsed_seconds'],
                'project': project.relative_to(root).as_posix(),
                'report': record.relative_to(root).as_posix(),
                'report_sha256': hashlib.sha256(record.read_bytes()).hexdigest(),
                'inputs': report['inputs']}
        if name in sources:
            item['original_cpu_source'] = sources[name]
        results.append(item)
        print(f"{name}: {item['status']} ({item['state_bits']} state bits): {item['reason']}", flush=True)
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    summary = {'schema_version': 1, 'scope': 'LEC only; per-design start contracts apply; no ISA correctness or physical signoff',
               'status': 'pass' if all(i['status'] == 'pass' for i in results) else 'fail', 'results': results}
    output.write_text(json.dumps(summary, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')
    return 0 if summary['status'] == 'pass' else 1


if __name__ == '__main__':
    raise SystemExit(main())
