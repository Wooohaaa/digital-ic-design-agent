"""Project opt-in to reviewed synchronous specification proof and evidence checks."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import subprocess
import uuid

from .evidence import hdl_input_hashes, local_file, sha, sha_lf
from .lec import find_yosys
from .project import load_manifest
from .spec_proof import Cover, Mutation, SpecContract, check_contract, run_spec, validate_report


REPORT = 'reports/specification.json'


@dataclass(frozen=True)
class SpecEvidence:
    required: bool
    status: str
    reason: str
    artifacts: dict[str, str]


def specification_config(manifest: dict) -> dict | None:
    if 'formal_specification' not in manifest:
        return None
    config = manifest['formal_specification']
    if (not isinstance(config, dict) or set(config) != {'required', 'contract', 'trusted_hashes'}
            or config['required'] is not True or not isinstance(config['contract'], str)
            or not isinstance(config['trusted_hashes'], dict)):
        raise ValueError('formal_specification requires required=true, a local contract and trusted hashes')
    return config


def project_contract(project: Path) -> tuple[SpecContract, dict[str, str]]:
    project = project.resolve()
    manifest = load_manifest(project)
    config = specification_config(manifest)
    if config is None:
        raise ValueError('No formal_specification selected')
    path = local_file(project, config['contract'])
    data = json.loads(path.read_text(encoding='utf-8'))
    expected = {'name', 'reference', 'reference_top', 'inputs', 'reset', 'active', 'projections', 'covers', 'mutations'}
    if not isinstance(data, dict) or not expected <= data.keys() <= expected | {'parameters'}:
        raise ValueError('Invalid specification contract fields')
    sources = manifest['rtl_sources']
    if not isinstance(sources, list) or not sources or any(not isinstance(name, str) for name in sources):
        raise ValueError('Specification gate requires an ordered list of project RTL compile units')
    units = [local_file(project, name) for name in sources]
    reference = local_file(project, data['reference'])
    if len({*units, reference, path}) != len(units)+2:
        raise ValueError('DUT, reference and specification contract must be separate files')
    trusted = {file.relative_to(project).as_posix(): sha_lf(file) for file in (path, reference)}
    if config['trusted_hashes'] != trusted:
        raise ValueError('Independent specification or contract differs from its reviewed hashes')
    fingerprints = {file.relative_to(project).as_posix(): sha(file) for file in (*units, reference, path)}
    if any(set(hdl_input_hashes(project, [file.relative_to(project).as_posix()]))
           != {file.relative_to(project).as_posix()} for file in (*units, reference)):
        raise ValueError('Specification gate supports self-contained HDL files only')
    parameters = data.get('parameters', {})
    if not isinstance(parameters, dict):
        raise ValueError('Expected an object of reviewed DUT parameter defaults')
    contract = SpecContract(
        name=data['name'], dut=units[0], reference=reference, dut_top=manifest['top_module'],
        reference_top=data['reference_top'], inputs=data['inputs'], reset=data['reset'],
        active=data['active'], projections=tuple(tuple(pair) for pair in data['projections']),
        covers={name: Cover(cover['signal'], cover['value'], cover['steps'],
                            tuple(tuple(pair) for pair in cover.get('stimulus', ())))
                for name, cover in data['covers'].items()},
        mutations={name: Mutation(mutation['before'].encode(), mutation['after'].encode(),
                                  mutation['obligation'], mutation['steps'], mutation.get('unit', 0))
                   for name, mutation in data['mutations'].items()},
        additional_dut=tuple(units[1:]), parameters=parameters,
    )
    check_contract(contract)
    return contract, fingerprints


def _runner() -> dict[str, str]:
    return {name: sha(Path(__file__).with_name(name)) for name in ('spec_gate.py', 'spec_proof.py')}


def _identity(project: Path, contract: SpecContract, inputs: dict) -> dict:
    return {'inputs': inputs, 'top': contract.dut_top,
            'rtl_sources': [path.relative_to(project).as_posix()
                            for path in (contract.dut, *contract.additional_dut)],
            'reference_sha256': sha(contract.reference), 'runner': _runner()}


def _report_digest(path: Path, content: bytes) -> str:
    digest = hashlib.sha256(content).hexdigest()
    if sha(path) != digest:
        raise ValueError('Specification evidence changed during validation')
    return digest


def run_project_spec(project: Path, *, yosys: str | None = None, timeout: int = 60) -> dict:
    from .quality import write_report
    project = project.resolve()
    contract, inputs = project_contract(project)
    if timeout <= 0:
        raise ValueError('Specification proof timeout must be positive')
    run = project/'build'/'specification'/uuid.uuid4().hex
    if run.resolve() != run or (project/REPORT).resolve() != project/REPORT:
        raise ValueError('Specification output paths must be inside the project without redirection')
    report = {'schema_version': 1, **_identity(project, contract, inputs), 'status': 'blocked',
              'run_directory': run.relative_to(project).as_posix(), 'reason': ''}
    tool = yosys or find_yosys()
    if tool is None:
        report['reason'] = 'Yosys is required for the specification gate'
    else:
        try:
            proof = run_spec(contract, run, yosys=tool, timeout=timeout)
            current, current_inputs = project_contract(project)
            if current != contract or any(report[key] != value for key, value in _identity(project, current, current_inputs).items()):
                raise ValueError('Specification inputs changed during proof')
            validate_report(current, run)
            report.update(status='pass', proof_sha256=sha(run/'result.json'),
                          reason='Current RTL satisfies the independent synchronous specification',
                          tool_version=proof['tool_version'])
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            report.update(status='fail', reason=str(exc))
    write_report(project, 'specification.json', report)
    return report


def specification_evidence(project: Path) -> SpecEvidence:
    project = project.resolve()
    required = False
    try:
        manifest = load_manifest(project)
        required = 'formal_specification' in manifest
        if not required:
            return SpecEvidence(False, 'not_run', 'No independent specification gate declared', {})
        contract, inputs = project_contract(project)
        if not (project/REPORT).exists():
            return SpecEvidence(True, 'missing', 'Missing independent specification evidence', {})
        path = local_file(project, REPORT)
        raw = path.read_bytes()
        report = json.loads(raw)
        if report['schema_version'] != 1 or any(report.get(k) != v for k, v in _identity(project, contract, inputs).items()):
            return SpecEvidence(True, 'stale', 'Specification inputs, project scope or checker changed', {})
        if report['status'] in ('fail', 'blocked'):
            return SpecEvidence(True, report['status'], 'Specification proof: '+report['reason'], {})
        if report['status'] != 'pass':
            raise ValueError('Unknown specification status')
        directory = report['run_directory']
        if not isinstance(directory, str) or len(directory.split('/')) != 3 or not directory.startswith('build/specification/'):
            raise ValueError('Unexpected specification evidence location')
        proof = local_file(project, directory+'/result.json')
        proof_raw = proof.read_bytes()
        if (proof.parent.relative_to(project).as_posix() != directory
                or hashlib.sha256(proof_raw).hexdigest() != report['proof_sha256']):
            raise ValueError('Specification proof report changed')
        validate_report(contract, proof.parent)
        details = json.loads(proof_raw)
        artifacts = {**inputs, REPORT: _report_digest(path, raw),
                     directory+'/result.json': _report_digest(proof, proof_raw)}
        artifacts.update({directory+'/'+name: digest for name, digest in details['artifacts'].items()})
        return SpecEvidence(True, 'current', report['reason'], artifacts)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        return SpecEvidence(required, 'stale', f'Invalid specification evidence or contract: {exc}', {})


def verify_archived_spec(archive, artifacts: dict[str, str]) -> None:
    for name, digest in artifacts.items():
        if hashlib.sha256(archive.read(name)).hexdigest() != digest:
            raise ValueError(f'Archived specification evidence differs: {name}')
