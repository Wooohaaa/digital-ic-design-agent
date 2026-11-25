"""Isolated slang parse/elaboration worker; this module is NOT a simulator."""
from __future__ import annotations

import json
import sys
from pathlib import Path


def check(job: dict) -> dict:
    try:
        import pyslang
        from pyslang.driver import Driver
    except ImportError:
        return {'status': 'blocked', 'reason': 'Install conformance extra (pyslang)', 'errors': []}
    if pyslang.__version__ != job['pyslang_version']:
        return {'status': 'blocked', 'reason': 'pyslang version differs from pinned profile',
                'version': pyslang.__version__, 'errors': []}
    driver = Driver()
    driver.addStandardArgs()
    driver.setTerminalColorsEnabled(False)
    driver.languageVersion = pyslang.LanguageVersion.v1800_2023
    args = ['slang', '--top', 'tb', *['-I'+p for p in job['includes']],
            *['-D'+name for name in job['defines']], *job['sources']]
    # Driver parses its own argv text; no shell is involved.
    command = ' '.join('"'+arg.replace('\\', '/').replace('"', '\\"')+'"' for arg in args)
    if not driver.parseCommandLine(command) or not driver.processOptions() or not driver.parseAllSources():
        return {'status': 'error', 'reason': 'slang driver could not load the declared inputs',
                'version': pyslang.__version__, 'errors': []}
    compilation = driver.createCompilation()
    compilation.getRoot()
    diagnostics = list(compilation.getAllDiagnostics())
    errors = [d for d in diagnostics if d.isError()]
    engine = pyslang.DiagnosticEngine(compilation.sourceManager)
    return {'status': 'rejected' if errors else 'accepted', 'version': pyslang.__version__,
            'errors': [{'code': str(d.code), 'message': engine.formatMessage(d)} for d in errors[:30]],
            'error_count': len(errors), 'warning_count': len(diagnostics)-len(errors)}


def main() -> int:
    path = Path(sys.argv[1])
    job = json.loads(path.read_text(encoding='utf-8'))
    try:
        result = check(job)
    except Exception as exc:
        result = {'status': 'error', 'reason': f'{type(exc).__name__}: {exc}', 'errors': []}
    Path(job['output']).write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    return 0 if result['status'] in {'accepted', 'rejected', 'blocked'} else 1


if __name__ == '__main__':
    raise SystemExit(main())
