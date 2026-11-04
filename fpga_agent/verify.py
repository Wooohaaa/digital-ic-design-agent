from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .project import load_manifest
from .evidence import EVIDENCE_VERSION, simulation_input_hashes


@dataclass
class SimulationResult:
    status: str
    simulator: str
    command: list[str]
    output: str
    returncode: int | None
    started_at: str
    elapsed_seconds: float
    source_hashes: dict[str, str]
    evidence_version: int = EVIDENCE_VERSION
    verification_inputs: dict[str, str] = field(default_factory=dict)
    compile_output: str = ''
    runtime_output: str = ''
    checks: list[dict] = field(default_factory=list)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_report(project: Path, result: SimulationResult) -> None:
    report_dir = project / "reports"
    report_dir.mkdir(exist_ok=True)
    (report_dir / "simulation.json").write_text(
        json.dumps(asdict(result), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (report_dir / "simulation.log").write_text(result.output, encoding="utf-8")


def _as_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def verify_project(project: Path, timeout: int = 120) -> SimulationResult:
    manifest = load_manifest(project)
    verification = manifest.get("verification", {})
    if isinstance(verification,dict) and verification.get('kind')=='tinystories-fp32':
        from .tinyllm import verify_project as verify_tiny
        report=verify_tiny(project)
        return SimulationResult(status=report['status'],simulator=report['simulator'],command=[],
            output=report.get('runtime_output',''),returncode=report['returncode'],
            started_at=report['started_at'],elapsed_seconds=report['elapsed_seconds'],
            source_hashes=report['source_hashes'],evidence_version=report['evidence_version'],
            verification_inputs=report['verification_inputs'],checks=report['checks'])
    if isinstance(verification, dict) and verification.get("kind") == "riscv-retire":
        # Import lazily so generic projects do not depend on the RV32 benchmark flow.
        from .cpu_bench import run_benchmark

        report = run_benchmark(project, seeds=verification.get("seeds"))
        return SimulationResult(
            status=report.get("status", "fail"),
            simulator=report.get("simulator", "Icarus Verilog"),
            command=report.get("command", []),
            output=report.get("output", ""),
            returncode=report.get("returncode", 0 if report.get("status") == "pass" else 1),
            started_at=report.get("started_at", ""),
            elapsed_seconds=float(report.get("elapsed_seconds", 0.0)),
            source_hashes=report.get("source_hashes", {}),
            evidence_version=report.get('evidence_version',0),
            verification_inputs=report.get('verification_inputs',{}),
        )
    sources = [project / item for item in manifest["rtl_sources"]]
    testbenches = [project / item for item in manifest["testbench_sources"]]
    all_sources = sources + testbenches
    for path in all_sources:
        if not path.is_file():
            raise FileNotFoundError(f"工程清单引用的 HDL 文件不存在：{path}")
    start_hashes={p.relative_to(project).as_posix():_sha(p) for p in all_sources}
    start_inputs = simulation_input_hashes(project, manifest)

    bundled_bin = Path(__file__).resolve().parent.parent / ".tools" / "icarus" / "bin"
    local_env = os.environ.copy()
    if bundled_bin.is_dir():
        local_env["PATH"] = str(bundled_bin) + os.pathsep + local_env.get("PATH", "")
    iverilog = shutil.which("iverilog", path=local_env.get("PATH"))
    vvp = shutil.which("vvp", path=local_env.get("PATH"))
    if not iverilog or not vvp:
        raise RuntimeError("没有找到 Icarus Verilog（iverilog/vvp）。请安装 Icarus Verilog 12+ 并重开终端。")

    output_dir = project / "build"
    output_dir.mkdir(exist_ok=True)
    executable = output_dir / "simulation.vvp"
    command = [iverilog, "-g2012", "-grelative-include", "-I", str(project), "-Wall", "-s", manifest["testbench_top"], "-o", str(executable)]
    command.extend(str(p) for p in all_sources)
    started = datetime.now(timezone.utc)
    start_clock = __import__("time").monotonic()
    compile_output = runtime_output = ''
    try:
        compile_result = subprocess.run(
            command,
            cwd=project,
            env=local_env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        compile_output = _as_text(compile_result.stdout) + _as_text(compile_result.stderr)
        output = compile_output
        returncode = compile_result.returncode
        if returncode == 0:
            run = subprocess.run(
                [vvp, str(executable)], cwd=project, env=local_env, capture_output=True, text=True, timeout=timeout, check=False
            )
            runtime_output = _as_text(run.stdout) + _as_text(run.stderr)
            output += runtime_output
            returncode = run.returncode
    except subprocess.TimeoutExpired as exc:
        output = _as_text(exc.stdout) + _as_text(exc.stderr) + f"\n仿真超时（>{timeout}s）。\n"
        returncode = 124
    elapsed = __import__("time").monotonic() - start_clock
    passed = returncode == 0 and bool(re.search(r"(?m)^\s*PASS(?:\b|:)", runtime_output)) and not re.search(
        r"(?im)^\s*(?:FAIL|FATAL|ERROR)(?:\b|:)", runtime_output
    )
    checks = []
    plan_path = project / 'spec/verification-plan.json'
    if plan_path.is_file():
        try:
            plan = json.loads(plan_path.read_text(encoding='utf-8'))
        except ValueError:
            plan = {}
        requirements = plan.get('requirements') if isinstance(plan,dict) else None
        if not isinstance(requirements,list) or not requirements:
            passed = False
            output += '\nVerification plan has no requirements.\n'
        else:
            markers = re.findall(r'(?m)^\s*CHECK\s+(\S+)\s+PASS\s*$', runtime_output)
            identifiers = [item.get('id') for item in requirements if isinstance(item,dict)]
            if len(identifiers) != len(requirements) or any(not isinstance(i,str) or not re.fullmatch(r'REQ-[0-9]{3,}',i) for i in identifiers) or len(set(identifiers)) != len(identifiers):
                passed = False
                output += '\nVerification plan requirement IDs are invalid or duplicated.\n'
            else:
                checks = [{'id':identifier,'status':'pass' if markers.count(identifier) == 1 else 'fail'} for identifier in identifiers]
                if any(item['status'] != 'pass' for item in checks) or set(markers) != set(identifiers):
                    passed = False
                    output += '\nRequirement checks missing, duplicated or outside the verification plan.\n'
    try:
        inputs_changed = simulation_input_hashes(project, load_manifest(project)) != start_inputs
    except (OSError, ValueError, KeyError):
        inputs_changed = True
    if inputs_changed:
        passed=False;output+='\nVerification inputs changed while simulation was running.\n'
    result = SimulationResult(
        status="pass" if passed else "fail",
        simulator="Icarus Verilog",
        command=command,
        output=output,
        returncode=returncode,
        started_at=started.isoformat(),
        elapsed_seconds=round(elapsed, 3),
        source_hashes=start_hashes,
        verification_inputs=start_inputs,
        compile_output=compile_output,
        runtime_output=runtime_output,
        checks=checks,
    )
    _write_report(project, result)
    return result
