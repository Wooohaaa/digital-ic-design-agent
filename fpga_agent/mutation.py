"""Isolated RTL mutation audit for measuring self-checking testbench strength."""
from __future__ import annotations

import hashlib
import heapq
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .evidence import hdl_input_hashes, simulation_input_hashes, trusted_testbench_valid
from .project import load_manifest
from .terminal import read_json, redact, write_json
from .verify import verify_project

_NON_CODE = re.compile(r'"(?:\\.|[^"\\])*"|//[^\r\n]*|/\*.*?\*/', re.S)
_OPERATORS = re.compile(r"===|!==|==|!=|&&|\|\||<=|>=|[+&|<>-]")
_REPLACEMENTS = {
    "==": "!=", "!=": "==", "&&": "||", "||": "&&",
    "+": "-", "-": "+", "&": "|", "|": "&", "<": ">", ">": "<",
}
_SPECIAL_PROFILES = {"hdmi-cube", "tinystories-fp32", "riscv-retire"}
_AUDIT_VERSION = 2


def _code_mask(text: str) -> str:
    """Blank comments/strings without shifting source offsets or line numbers."""
    return _NON_CODE.sub(lambda match: "".join("\n" if char == "\n" else " " for char in match.group()), text)


def _candidate_id(path: str, line: int, column: int, old: str, new: str) -> str:
    payload = f"{_AUDIT_VERSION}\0{path}\0{line}\0{column}\0{old}\0{new}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _mutants(project: Path, manifest: dict, limit: int, seed: int) -> tuple[list[dict], int]:
    reservoir: list[tuple[int, str, dict]] = []
    total = 0
    seen_files: set[str] = set()
    for name in manifest["rtl_sources"]:
        name = Path(name).as_posix()
        if Path(name).is_absolute() or ".." in Path(name).parts:
            raise ValueError(f"RTL source path must stay relative to the project: {name}")
        if name in seen_files:
            continue
        seen_files.add(name)
        path = (project / name).resolve()
        if project.resolve() not in path.parents or not path.is_file():
            raise ValueError(f"RTL source is missing or escapes the project: {name}")
        if path.suffix.lower() not in {".v", ".sv"}:
            continue
        if path.stat().st_size > 4_000_000:
            raise ValueError(f"RTL file exceeds the 4 MB audit limit: {name}")
        text = path.read_text(encoding="utf-8")
        masked = _code_mask(text)
        for match in _OPERATORS.finditer(masked):
            old = match.group(0)
            new = _REPLACEMENTS.get(old)
            if new is None:
                continue
            start, end = match.span()
            # Avoid compound assignment and event-trigger tokens that the
            # lightweight lexer cannot classify confidently.
            if old in {"&", "|"} and (masked[end:end + 1] == "=" or masked[start - 1:start] in {"&", "|"}):
                continue
            if old in {"+", "-"} and (masked[end:end + 1] == old or masked[start - 1:start] == old):
                continue
            if old in {"<", ">"} and (masked[end:end + 1] == old or masked[start - 1:start] == old):
                continue
            if old == "|" and masked[end:end + 2] == "->":
                continue
            if old == "-" and masked[end:end + 1] == ">":
                continue
            if old == ">" and masked[start - 1:start] == "-":
                continue
            total += 1
            line = masked.count("\n", 0, start) + 1
            line_start = masked.rfind("\n", 0, start) + 1
            column = start - line_start + 1
            identity = _candidate_id(name, line, column, old, new)
            rank = int(hashlib.sha256(f"{seed}\0{identity}".encode("utf-8")).hexdigest(), 16)
            candidate = {"id": identity, "file": name, "line": line, "column": column,
                         "old": old, "new": new, "offset": start, "end": end,
                         "source_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}
            entry = (-rank, identity, candidate)
            if len(reservoir) < limit:
                heapq.heappush(reservoir, entry)
            elif entry > reservoir[0]:
                heapq.heapreplace(reservoir, entry)
    candidates = [item[2] for item in reservoir]
    candidates.sort(key=lambda item: (item["file"], item["line"], item["column"]))
    return candidates, total


def simulator_fingerprint() -> dict:
    environment = os.environ.copy()
    bundled = Path(__file__).resolve().parent.parent / ".tools" / "icarus" / "bin"
    if bundled.is_dir():
        environment["PATH"] = str(bundled) + os.pathsep + environment.get("PATH", "")
    result = {}
    for name in ("iverilog", "vvp"):
        executable = shutil.which(name, path=environment.get("PATH"))
        if not executable:
            return {"status": "unavailable", "missing": name}
        try:
            probe = subprocess.run([executable, "-V"], capture_output=True, text=True,
                                   timeout=5, check=False, env=environment)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"status": "unavailable", "missing": name, "reason": type(exc).__name__}
        version = (probe.stdout + "\n" + probe.stderr).strip()
        result[name] = hashlib.sha256(version.encode("utf-8", errors="replace")).hexdigest()
    return {"status": "available", "sha256": result}


def _shadow_project(project: Path, inputs: dict, destination: Path) -> Path:
    root = project.resolve()
    shadow = destination / "project"
    shadow.mkdir(parents=True)
    manifest_path = project / "project.json"
    if manifest_path.is_symlink():
        raise ValueError("Refusing to audit a project.json symlink.")
    shutil.copy2(manifest_path, shadow / "project.json")
    names = [name for name in inputs if not name.startswith("@")]
    total_bytes = 0
    for name in names:
        source = (project / name).resolve()
        if root not in source.parents or not source.is_file():
            raise ValueError(f"Verification input is missing or escapes the project: {name}")
        total_bytes += source.stat().st_size
        if total_bytes > 128_000_000:
            raise ValueError("Transitive verification inputs exceed the 128 MB audit limit.")
        target = shadow / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return shadow


def _result_status(result) -> str:
    if result.status == "pass":
        return "survived"
    if result.returncode == 124:
        return "timeout"
    if result.returncode not in (None, 0) and not result.runtime_output:
        return "compile_error"
    if re.search(r"(?im)^\s*(?:FAIL|FATAL|ERROR)(?:\b|:)", result.runtime_output):
        return "killed"
    return "inconclusive"


def mutation_audit(project: Path, *, max_mutants: int = 128, timeout: int = 30,
                   total_timeout: int = 1800, seed: int = 1,
                   progress: Callable[[str], None] | None = None) -> dict:
    """Run deterministic single-token mutants in a disposable project copy.

    The original RTL, testbench, build tree and simulation report are never
    modified. Progress is checkpointed to reports/mutation.json so a rerun can
    continue after an interrupted long audit.
    """
    if type(max_mutants) is not int or not 1 <= max_mutants <= 1000:
        raise ValueError("max_mutants must be 1..1000")
    if type(timeout) is not int or not 1 <= timeout <= 600:
        raise ValueError("timeout must be 1..600 seconds")
    if type(total_timeout) is not int or not 10 <= total_timeout <= 86400:
        raise ValueError("total_timeout must be 10..86400 seconds")
    if type(seed) is not int or not 0 <= seed <= 2**31 - 1:
        raise ValueError("seed must be 0..2147483647")
    project = project.resolve()
    manifest = load_manifest(project)
    profile = manifest.get("verification", {}).get("kind")
    if profile in _SPECIAL_PROFILES:
        raise ValueError(f"The {profile} profile has a specialized verifier; this generic Icarus audit is not applicable.")
    if not manifest.get("rtl_sources") or not manifest.get("testbench_sources"):
        raise ValueError("Mutation audit requires RTL sources and a self-checking Icarus testbench.")
    if profile == "trusted-testbench":
        trusted, reason = trusted_testbench_valid(project, manifest)
        if not trusted:
            raise ValueError("Trusted testbench pin check failed: " + reason)

    inputs = simulation_input_hashes(project, manifest)
    tools = simulator_fingerprint()
    candidates, candidate_total = _mutants(project, manifest, max_mutants, seed)
    if not candidates:
        raise ValueError("No supported mutation opportunities were found in the declared Verilog/SystemVerilog sources.")
    selected = candidates
    audit_config = {"version": _AUDIT_VERSION, "simulation_inputs": inputs, "tools": tools,
                    "max_mutants": max_mutants, "timeout": timeout,
                    "seed": seed, "candidate_ids": [item["id"] for item in selected]}
    fingerprint = hashlib.sha256(json.dumps(audit_config, sort_keys=True).encode("utf-8")).hexdigest()
    report_path = project / "reports" / "mutation.json"
    previous = read_json(report_path, {})
    if isinstance(previous, dict) and previous.get("fingerprint") == fingerprint and previous.get("status") == "complete":
        return previous
    previous_results = {}
    started_at = datetime.now(timezone.utc).isoformat()
    if isinstance(previous, dict) and previous.get("fingerprint") == fingerprint:
        started_at = previous.get("started_at", started_at)
        previous_results = {item.get("id"): item for item in previous.get("mutants", [])
                            if isinstance(item, dict) and item.get("id") in {candidate["id"] for candidate in selected}
                            and item.get("status") in {"killed", "survived", "compile_error", "timeout", "inconclusive"}}
    if progress:
        progress(f"Mutation audit: {len(previous_results)}/{len(selected)} previously checkpointed; validating baseline.")
    report = {"schema_version": 1, "audit_version": _AUDIT_VERSION, "project": manifest.get("project", project.name),
              "status": "running", "started_at": started_at, "updated_at": datetime.now(timezone.utc).isoformat(),
              "fingerprint": fingerprint, "simulation_inputs": inputs, "simulator_fingerprint": tools,
              "testbench_profile": profile or "generic-self-checking",
              "candidate_count": candidate_total, "selected_count": len(selected),
              "candidate_selection_truncated": candidate_total > len(selected), "max_mutants": max_mutants, "seed": seed,
              "timeout_seconds": timeout, "total_timeout_seconds": total_timeout,
              "mutants": [previous_results[item["id"]] for item in selected if item["id"] in previous_results],
              "note": "Mutation score is diagnostic only. Survivors may be equivalent; compilation failures and inconclusive runs are excluded."}
    write_json(report_path, report)
    try:
        with tempfile.TemporaryDirectory(prefix="icforge-mutation-") as temporary:
            shadow = _shadow_project(project, inputs, Path(temporary))
            baseline = verify_project(shadow, timeout=timeout)
            report["baseline"] = {"status": baseline.status, "returncode": baseline.returncode,
                                  "elapsed_seconds": baseline.elapsed_seconds,
                                  "output_tail": redact(baseline.output[-1600:], dict(os.environ))}
            if baseline.status != "pass":
                report.update(status="blocked", reason="Baseline self-check did not pass in the isolated project copy.",
                              updated_at=datetime.now(timezone.utc).isoformat())
                write_json(report_path, report)
                if progress:
                    progress("Mutation audit blocked: isolated baseline did not pass.")
                return report
            if progress:
                progress(f"Mutation audit baseline PASS; running up to {len(selected)} mutants.")

            result_by_id = {item["id"]: item for item in report["mutants"]}
            start_clock = time.monotonic()
            deadline = start_clock + total_timeout
            for candidate in selected:
                if candidate["id"] in result_by_id:
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    report["status"] = "partial"
                    break
                source_path = shadow / candidate["file"]
                original = source_path.read_text(encoding="utf-8")
                if hashlib.sha256(original.encode("utf-8")).hexdigest() != candidate["source_sha256"]:
                    raise ValueError(f"Isolated RTL source changed before mutation: {candidate['file']}")
                mutated = original[:candidate["offset"]] + candidate["new"] + original[candidate["end"]:]
                source_path.write_text(mutated, encoding="utf-8", newline="\n")
                run_started = time.monotonic()
                try:
                    simulation = verify_project(shadow, timeout=max(1, min(timeout, int(remaining))))
                    status = _result_status(simulation)
                    output = simulation.runtime_output or simulation.compile_output or simulation.output
                    item = {key: candidate[key] for key in ("id", "file", "line", "column", "old", "new")}
                    item.update(status=status, elapsed_seconds=simulation.elapsed_seconds,
                                output_tail=redact(output[-1600:], dict(os.environ)))
                except (OSError, ValueError, RuntimeError) as exc:
                    status = "inconclusive"
                    item = {key: candidate[key] for key in ("id", "file", "line", "column", "old", "new")}
                    item.update(status="inconclusive", elapsed_seconds=round(time.monotonic() - run_started, 3),
                                output_tail=redact(f"{type(exc).__name__}: {exc}", dict(os.environ))[-1600:])
                finally:
                    source_path.write_text(original, encoding="utf-8", newline="\n")
                result_by_id[candidate["id"]] = item
                report["mutants"] = [result_by_id[item["id"]] for item in selected if item["id"] in result_by_id]
                report["completed_count"] = len(report["mutants"])
                report["updated_at"] = datetime.now(timezone.utc).isoformat()
                write_json(report_path, report)
                if progress:
                    progress(f"Mutation {len(report['mutants'])}/{len(selected)}: {status} {candidate['file']}:{candidate['line']} {candidate['old']}→{candidate['new']}")
            if report["status"] == "running":
                report["status"] = "complete"
            report["elapsed_seconds"] = round(time.monotonic() - start_clock, 3)
    except (OSError, ValueError, RuntimeError) as exc:
        report.update(status="blocked", reason=redact(f"{type(exc).__name__}: {exc}", dict(os.environ))[:2000])

    counts = {status: sum(item.get("status") == status for item in report["mutants"])
              for status in ("killed", "survived", "compile_error", "timeout", "inconclusive")}
    denominator = counts["killed"] + counts["survived"]
    report["counts"] = counts
    report["observed_kill_rate_percent"] = round(100 * counts["killed"] / denominator, 2) if denominator else None
    report["completed_count"] = len(report["mutants"])
    report["updated_at"] = datetime.now(timezone.utc).isoformat()
    write_json(report_path, report)
    if progress:
        progress(f"Mutation audit {report['status']}: {counts['killed']} killed, {counts['survived']} survived, "
                 f"{counts['compile_error']} compile-invalid, {counts['timeout']} timeout, {counts['inconclusive']} inconclusive.")
    return report
