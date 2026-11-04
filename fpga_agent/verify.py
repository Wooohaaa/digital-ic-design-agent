from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .project import load_manifest


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
        )
    sources = [project / item for item in manifest["rtl_sources"]]
    testbenches = [project / item for item in manifest["testbench_sources"]]
    all_sources = sources + testbenches
    for path in all_sources:
        if not path.is_file():
            raise FileNotFoundError(f"工程清单引用的 HDL 文件不存在：{path}")
    start_hashes={p.relative_to(project).as_posix():_sha(p) for p in all_sources}

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
    command = [iverilog, "-g2012", "-Wall", "-s", manifest["testbench_top"], "-o", str(executable)]
    command.extend(str(p) for p in all_sources)
    started = datetime.now(timezone.utc)
    start_clock = __import__("time").monotonic()
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
        output = _as_text(compile_result.stdout) + _as_text(compile_result.stderr)
        returncode = compile_result.returncode
        if returncode == 0:
            run = subprocess.run(
                [vvp, str(executable)], cwd=project, env=local_env, capture_output=True, text=True, timeout=timeout, check=False
            )
            output += _as_text(run.stdout) + _as_text(run.stderr)
            returncode = run.returncode
    except subprocess.TimeoutExpired as exc:
        output = _as_text(exc.stdout) + _as_text(exc.stderr) + f"\n仿真超时（>{timeout}s）。\n"
        returncode = 124
    elapsed = __import__("time").monotonic() - start_clock
    passed = returncode == 0 and bool(re.search(r"(?m)^\s*PASS(?:\b|:)", output)) and not re.search(
        r"(?m)^\s*FAIL(?:\b|:)", output
    )
    if any(_sha(project/name)!=expected for name,expected in start_hashes.items()):
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
    )
    _write_report(project, result)
    return result
