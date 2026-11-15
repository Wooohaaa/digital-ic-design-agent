"""Clock/reset domain crossing (CDC/RDC) structural analysis.

Stage 4 adds a *structural heuristic* that looks for signals driven in one
clocked process and read in another clocked process with a different clock.

Honesty contract (see ``docs/cdc.md``):

* This is a source-level heuristic, not a formal CDC proof. It does not reason
  about synchronizers, multi-cycle paths, set/reset interactions or gate-level
  nets. It reports *candidates* for engineering review.
* A design with no clocked process is reported ``pass`` (nothing to cross); a
  design with crossings is reported ``review_required`` -- never ``pass``.
* Missing or unreadable RTL is reported ``blocked``, never silently skipped.
* Reset-domain information is reported for review; an asynchronous reset used
  across clock domains is a candidate, not a verdict.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .evidence import sha
from .project import load_manifest
from .terminal import write_json
from .yosys import absolute_sources

CDC_VERSION = 1

_RESET_RE = re.compile(r'(rst|reset|clr|clear)', re.I)
_BLOCK_COMMENT = re.compile(r'/\*.*?\*/', re.S)
_LINE_COMMENT = re.compile(r'//[^\n]*')
_ALWAYS = re.compile(
    r'\balways\s*(?:_ff|_comb|_latch)?\s*@\s*\(([^)]*)\)\s*(begin|;)', re.I)
_INITIAL = re.compile(r'\binitial\s*(begin|;)', re.I)
_ASSIGN = re.compile(
    r'(?<![=!<>])([A-Za-z_]\w*)(?:\s*\[[^\]]*\])*\s*(?:<=|=(?!=))\s*')
_CONTINUOUS = re.compile(
    r'\bassign\s+([A-Za-z_]\w*)(?:\s*\[[^\]]*\])*\s*(?:<=|=(?!=))\s*', re.I)
_IDENT = re.compile(r'[A-Za-z_]\w*')
_EDGE = re.compile(r'(?:posedge|negedge)\s+([A-Za-z_]\w*)', re.I)
_MODULE = re.compile(r'\bmodule\s+([A-Za-z_]\w*)', re.I)

_KEYWORDS = {
    'always', 'initial', 'begin', 'end', 'if', 'else', 'for', 'while',
    'case', 'casex', 'casez', 'endcase', 'assign', 'wire', 'reg', 'logic',
    'output', 'input', 'inout', 'integer', 'signed', 'unsigned', 'localparam',
    'parameter', 'generate', 'endgenerate', 'function', 'task', 'buf',
    'not', 'and', 'or', 'nor', 'nand', 'xor', 'xnor', 'bufif0', 'bufif1',
    'ifnone', 'default', 'join', 'fork', 'forever', 'disable', 'force',
    'release', 'specparam', 'property', 'cover', 'assert', 'assume',
    'class', 'new', 'this', 'super', 'void', 'return', 'import', 'package',
    'virtual', 'const', 'static', 'automatic', 'protected', 'public',
    'rand', 'randc', 'sequence', 'clocking', 'interface', 'modport',
    'alias', 'let', 'typedef', 'enum', 'struct', 'union', 'event',
    'var', 'byte', 'shortint', 'int', 'longint', 'shortreal', 'real',
    'time', 'string', 'bit', 'logic', 'logic', 'realtime', 'yield',
    'dist', 'soft', 'hard', 'unique', 'priority', '&&', '||', '!',
}


def _strip_comments(text: str) -> str:
    text = _BLOCK_COMMENT.sub(' ', text)
    text = _LINE_COMMENT.sub(' ', text)
    return text


def _split_processes(text: str) -> list[dict]:
    """Return clocked/combinational/initial processes with body and line."""
    stripped = _strip_comments(text)
    processes: list[dict] = []
    matches = list(_ALWAYS.finditer(stripped)) + list(_INITIAL.finditer(stripped))
    matches.sort(key=lambda m: m.start())
    for match in matches:
        header_end = match.end()
        sensitivity = match.group(1) if match.re is _ALWAYS else ''
        # The header regex may already consume the opening `begin`.
        if match.group(0).rstrip().endswith('begin'):
            depth = 1
            started = True
        else:
            depth = 0
            started = False
        # Find the matching `end` accounting for begin/end nesting.
        index = header_end
        while index < len(stripped):
            if stripped.startswith('begin', index) and not (
                    index + 5 < len(stripped)
                    and (stripped[index + 5].isalnum() or stripped[index + 5] == '_')):
                depth += 1
                started = True
                index += 5
                continue
            if stripped.startswith('end', index) and not (
                    index + 3 < len(stripped)
                    and (stripped[index + 3].isalnum() or stripped[index + 3] == '_')):
                depth -= 1
                index += 3
                if started and depth == 0:
                    break
                continue
            index += 1
        body = stripped[header_end:index]
        processes.append({
            'sensitivity': sensitivity,
            'body': body,
            'line': stripped[:match.start()].count('\n') + 1,
        })
    return processes


def _clock_domain(sensitivity: str) -> tuple[str, ...]:
    clocks = []
    for edge in _EDGE.finditer(sensitivity):
        name = edge.group(1)
        if _RESET_RE.search(name):
            # A reset signal is a reset, not a clock.
            continue
        if name not in clocks:
            clocks.append(name)
    return tuple(clocks)


def _reset_signals(sensitivity: str, body: str) -> list[str]:
    resets: list[str] = []
    for edge in _EDGE.finditer(sensitivity):
        name = edge.group(1)
        if _RESET_RE.search(name) and name not in resets:
            resets.append(name)
    for ident in _IDENT.findall(body):
        if _RESET_RE.search(ident) and ident not in resets:
            resets.append(ident)
    return resets


def _base_name(identifier: str) -> str:
    return identifier.split('[', 1)[0]


def _analyze_source(path: Path, project: Path) -> dict:
    text = _strip_comments(path.read_text(encoding='utf-8', errors='replace'))
    module = None
    module_match = _MODULE.search(text)
    if module_match:
        module = module_match.group(1)
    drivers: dict[str, set[tuple[str, ...]]] = {}
    readers: dict[str, set[tuple[str, ...]]] = {}
    domains: list[dict] = []
    processes = _split_processes(text)
    # Continuous assignments belong to no clock domain.
    for assign in _CONTINUOUS.finditer(text):
        signal = _base_name(assign.group(1))
        drivers.setdefault(signal, set()).add(())
        rhs = text[assign.end():]
        end = rhs.find(';')
        expression = rhs[:end] if end >= 0 else rhs
        for ident in _IDENT.findall(expression):
            if ident not in _KEYWORDS:
                readers.setdefault(_base_name(ident), set()).add(())
    for process in processes:
        domain = _clock_domain(process['sensitivity'])
        resets = _reset_signals(process['sensitivity'], process['body'])
        body_idents = [ident for ident in _IDENT.findall(process['body'])
                       if ident not in _KEYWORDS]
        assigned = set()
        for assign in _ASSIGN.finditer(process['body']):
            signal = _base_name(assign.group(1))
            if signal in _KEYWORDS:
                continue
            assigned.add(signal)
            drivers.setdefault(signal, set()).add(domain)
        for ident in body_idents:
            base = _base_name(ident)
            if base in _KEYWORDS:
                continue
            readers.setdefault(base, set()).add(domain)
        if domain or resets:
            domains.append({
                'clocks': list(domain),
                'resets': resets,
                'module': module,
                'source': path.relative_to(project).as_posix(),
                'line': process['line'],
            })
    return {'drivers': drivers, 'readers': readers, 'domains': domains,
            'module': module}


def _crossings(analyses: list[dict]) -> list[dict]:
    drivers: dict[str, set[tuple[str, ...]]] = {}
    readers: dict[str, set[tuple[str, ...]]] = {}
    domains: list[dict] = []
    for analysis in analyses:
        for signal, domains_set in analysis['drivers'].items():
            drivers.setdefault(signal, set()).update(domains_set)
        for signal, domains_set in analysis['readers'].items():
            readers.setdefault(signal, set()).update(domains_set)
        domains.extend(analysis['domains'])
    crossings: list[dict] = []
    for signal in sorted(set(drivers) & set(readers)):
        for from_domain in sorted(drivers[signal]):
            if not from_domain:
                continue
            for to_domain in sorted(readers[signal]):
                if not to_domain or to_domain == from_domain:
                    continue
                kind = 'cdc'
                if len(drivers[signal]) > 1:
                    kind = 'multi_driver'
                crossings.append({
                    'signal': signal,
                    'from_clock': '|'.join(from_domain),
                    'to_clock': '|'.join(to_domain),
                    'kind': kind,
                })
    crossings.sort(key=lambda item: (item['signal'], item['from_clock'],
                                     item['to_clock']))
    return crossings, domains


def analyze_cdc(project: Path, sources: list[str]) -> dict:
    project = project.resolve()
    analyses = []
    for source in sources:
        relative = absolute_sources(project, [source], label='cdc sources')[0]
        path = project / relative
        analyses.append(_analyze_source(path, project))
    crossings, domains = _crossings(analyses)
    clocks = sorted({clock for item in domains for clock in item['clocks']})
    reset_domains = sorted({
        (tuple(item['clocks']), tuple(sorted(item['resets'])))
        for item in domains if item['resets']
    })
    return {
        'kind': 'cdc',
        'clocks': clocks,
        'domains': domains,
        'crossings': crossings,
        'reset_domains': [
            {'clocks': list(clocks_), 'resets': list(resets_)}
            for clocks_, resets_ in reset_domains
        ],
    }


def _cdc_inputs(project: Path, sources: list[str]) -> dict:
    from .evidence import hdl_input_hashes
    result = hdl_input_hashes(project, sources)
    result['@checker'] = sha(Path(__file__))
    config = {'sources': sources, 'version': CDC_VERSION}
    result['@cdc_config'] = hashlib.sha256(
        json.dumps(config, sort_keys=True).encode('utf-8')).hexdigest()
    return result


def run_cdc(project: Path, *, timeout: int = 60) -> dict:
    """Analyse RTL sources for clock/reset domain crossing candidates."""
    if type(timeout) is not int or not 1 <= timeout <= 86400:
        raise ValueError('timeout must be 1..86400 seconds')
    project = project.resolve()
    manifest = load_manifest(project)
    sources = manifest.get('rtl_sources')
    if not isinstance(sources, list) or not sources:
        report = {
            'schema_version': 1, 'cdc_version': CDC_VERSION, 'kind': 'cdc',
            'project': manifest.get('project', project.name), 'status': 'blocked',
            'reason': 'No RTL sources declared in the manifest.',
            'sources': [], 'clocks': [], 'domains': [], 'crossings': [],
            'reset_domains': [], 'inputs': {}, 'started_at':
                datetime.now(timezone.utc).isoformat(), 'elapsed_seconds': 0.0,
        }
        write_json(project / 'reports/cdc.json', report)
        return report
    sources = absolute_sources(project, sources, label='cdc sources')
    analysis = analyze_cdc(project, sources)
    inputs = _cdc_inputs(project, sources)
    report = {
        'schema_version': 1, 'cdc_version': CDC_VERSION, 'kind': 'cdc',
        'project': manifest.get('project', project.name),
        'sources': sources, 'clocks': analysis['clocks'],
        'domains': analysis['domains'], 'crossings': analysis['crossings'],
        'reset_domains': analysis['reset_domains'], 'inputs': inputs,
        'started_at': datetime.now(timezone.utc).isoformat(),
        'elapsed_seconds': 0.0,
    }
    if analysis['crossings']:
        report['status'] = 'review_required'
        report['reason'] = (
            f"{len(analysis['crossings'])} clock/reset domain crossing "
            f"candidate(s) require review: "
            + '; '.join(
                f"{item['signal']} ({item['from_clock']} -> {item['to_clock']})"
                for item in analysis['crossings'][:5])
            + ('.' if len(analysis['crossings']) <= 5
               else f" (+{len(analysis['crossings']) - 5} more)."))
    else:
        report['status'] = 'pass'
        report['reason'] = (
            'No clocked-process clock/reset domain crossing candidates found.'
            if analysis['clocks']
            else 'No clocked processes found; nothing to cross.')
    write_json(project / 'reports/cdc.json', report)
    (project / 'reports/cdc.log').write_text(report['reason'] + '\n',
                                             encoding='utf-8')
    return report


def cdc_valid(project: Path, manifest: dict, report: dict) -> tuple[bool, str]:
    if not isinstance(report, dict):
        return False, 'CDC report is not an object'
    if report.get('kind') != 'cdc':
        return False, 'CDC report kind mismatch'
    sources = manifest.get('rtl_sources')
    if not isinstance(sources, list) or not sources:
        return False, 'manifest declares no RTL sources'
    try:
        resolved = absolute_sources(project, sources, label='cdc sources')
        if report.get('inputs') != _cdc_inputs(project, resolved):
            return False, 'RTL changed since the CDC run'
    except (OSError, ValueError, KeyError) as exc:
        return False, str(exc)
    if report.get('status') == 'pass':
        return True, 'No clock/reset domain crossing candidates'
    if report.get('status') == 'review_required':
        return False, f"CDC candidates require review ({report.get('reason')})"
    return False, f"CDC has not passed ({report.get('status')})"
