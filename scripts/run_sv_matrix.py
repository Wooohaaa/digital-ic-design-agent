"""Reproduce each runtime separately and retain disagreements, including gaps."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from fpga_agent.evidence import sha
from fpga_agent.sv_conformance import BACKENDS, conformance_valid, run_conformance


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--backends', nargs='+', choices=BACKENDS, default=list(BACKENDS))
    parser.add_argument('--timeout', type=int, default=120)
    args = parser.parse_args()
    if len(set(args.backends)) != len(args.backends):
        parser.error('Duplicate backends')
    output = args.output.resolve()
    entries, reports = {}, []
    for backend in args.backends:
        path = output.with_name(output.stem+'-'+backend+'.json')
        report = run_conformance(path, backend=backend, timeout=args.timeout)
        valid, reason = conformance_valid(path)
        entries[backend] = {'report': path.name, 'sha256': sha(path), 'status': report['status'],
                            'baseline_status': report['baseline_status'], 'evidence_valid': valid,
                            'uvm_fixture_profile_status': report['uvm_fixture_profile_status'],
                            'validation_reason': reason, 'version': report['runtime_compiler_version'],
                            'feature_counts': dict(Counter(f['status'] for f in report['features']))}
        reports.append(report)
        print(f'{backend}: {report["status"]}, baseline={report["baseline_status"]}, evidence_valid={valid}', flush=True)
    same_inputs = all(r['inputs'] == reports[0]['inputs'] for r in reports)
    cases = []
    for row in reports[0]['cases']:
        states = {r['backend']: next(c['status'] for c in r['cases'] if c['id'] == row['id']) for r in reports}
        cases.append({'id': row['id'], 'backends': states,
                      'all_selected_backends_pass': len(states) >= 2 and all(s == 'pass' for s in states.values()),
                      'capability_difference': len(set(states.values())) > 1})
    status = ('fail' if not same_inputs or any(not e['evidence_valid'] or e['status']=='fail' for e in entries.values())
              else 'pass' if all(e['status']=='pass' for e in entries.values()) else 'partial')
    summary = {'schema_version': 1, 'created_at': datetime.now(timezone.utc).isoformat(), 'status': status,
               'scope': 'Separate bounded backend observations; no automatic fallback or capability union',
               'full_sv_uvm_complete': False, 'uvm_minimum_complete': False,
               'same_inputs': same_inputs, 'runner_sha256': sha(Path(__file__)),
               'backends': entries, 'cases': cases}
    output.write_text(json.dumps(summary, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')
    print(f'Evidence: {output}', flush=True)
    return 0 if status == 'pass' else 1 if status == 'fail' else 2


if __name__ == '__main__':
    raise SystemExit(main())
