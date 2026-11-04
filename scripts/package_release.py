"""Package source, tests, model provenance and verified CPU deliveries; no tools/intermediates."""
from pathlib import Path
import hashlib,json,zipfile,sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from fpga_agent import __version__
out=ROOT/'deliverables';out.mkdir(exist_ok=True)
files=set()
for name in ('README.md','pyproject.toml','.gitignore'):
    files.add(ROOT/name)
for directory in ('fpga_agent','tests','docs','scripts'):
    for path in (ROOT/directory).rglob('*'):
        if path.is_file() and '__pycache__' not in path.parts and path.suffix in {'.py','.md','.json','.v','.sv','.adc','.sdc','.tcss','.png'}:files.add(path)
for name in ('rv32_single','rv32_multi','rv32_pipeline'):
    project=ROOT/'benchmarks'/name
    files.add(project/'project.json')
    for directory in ('rtl','tb','constraints','board','spec','generation','deliverables'):
        for path in (project/directory).rglob('*'):
            if path.is_file() and path.name != 'verification.json' and path.suffix in {'.v','.sv','.json','.md','.adc','.sdc','.txt','.zip','.hex'}:files.add(path)
failure=ROOT/'benchmarks/failures/rv32_multi_initial'
for rel in ('rtl/rv32_core.v','project.json','build/cpu/alu_edges/program.hex','build/cpu/alu_edges/expected.json'):
    if (failure/rel).is_file():files.add(failure/rel)
files.add(ROOT/'benchmarks/suite-report.json')
for path in (ROOT/'reports').glob('*'):
    if path.is_file() and path.name in {'agent-regression.json','wheel-smoke.json'}:files.add(path)
for wheel in (ROOT/'dist').glob(f'*{__version__}*.whl'):files.add(wheel)
manifest={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)}
archive=out/f'digital-ic-agent-v{__version__}.zip'
with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as z:
    for path in sorted(files):z.write(path,path.relative_to(ROOT))
    z.writestr('release-manifest.json',json.dumps(manifest,indent=2)+'\n')
with zipfile.ZipFile(archive) as z:
    assert z.testzip() is None
    for name,expected in manifest.items():assert hashlib.sha256(z.read(name)).hexdigest()==expected,name
print(archive,archive.stat().st_size,'bytes',len(manifest),'files')
