from dataclasses import replace
import json
from pathlib import Path
import runpy

import pytest

from fpga_agent.lec import find_yosys
from fpga_agent.spec_proof import run_spec, validate_report


CONTRACTS = runpy.run_path(str(Path(__file__).resolve().parents[1]/'scripts/run_spec_checks.py'))['CONTRACTS']


@pytest.mark.skipif(find_yosys() is None, reason='Yosys or YoWASP is required')
@pytest.mark.parametrize('name', ['fifo', 'counter'])
def test_spec_acceptance_runs_proofs_and_checks_retained_evidence(tmp_path, name):
    contract = CONTRACTS[name]
    output = tmp_path / name
    run_spec(contract, output)
    validate_report(contract, output)

    with pytest.raises(ValueError, match='Stale or incompatible'):
        validate_report(replace(contract, active=1-contract.active), output)

    result = output / 'result.json'
    original = result.read_bytes()
    report = json.loads(original)
    del report['verification']['original']['checks']['preservation']
    result.write_text(json.dumps(report), encoding='utf-8')
    with pytest.raises(ValueError, match='Missing or extra specification obligation'):
        validate_report(contract, output)
    result.write_bytes(original)

    witness = next(output.glob('*/reset_trace-witness.json'))
    witness.write_text('{}', encoding='utf-8')
    with pytest.raises(ValueError, match='evidence changed'):
        validate_report(contract, output)
