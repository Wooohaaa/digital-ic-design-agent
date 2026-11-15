"""Minimal class-based (UVM-style) verification environment evidence.

The mandatory gate for a generic project is a self-checking Icarus simulation
(:mod:`fpga_agent.verify`). Icarus, however, does not parse SystemVerilog
*classes*, so a class-based environment -- a randomized transaction, a driver
bound to a ``virtual interface`` and a self-checking scoreboard -- cannot run
there. Verilator can both elaborate those classes and build an executable, so
this module runs that environment with ``verilator --binary`` and records hashed
evidence for it.

This is deliberately *not* a UVM library. It is a small, self-authored
transaction/driver/scoreboard set. It answers one question with hashed evidence:
*does the class-based environment elaborate and execute against the RTL?*

Honesty contract (see ``docs/svsim.md``):

* A pass means the environment built and ran to completion with one ``CHECK
  REQ-NNN PASS`` marker per requirement in ``spec/verification-plan.json`` and
  no ``FAIL``/``FATAL``/``ERROR`` line. It does not measure functional coverage.
* When Verilator, a C++ compiler or ``make`` is unavailable the gate reports
  ``blocked`` instead of pretending success; a build failure is reported as
  ``error`` and a failing run as ``fail``.
* Editing any declared source, the specification or the verification plan
  invalidates the recorded input hashes, so a stale report is never read as
  current.
* ``-Wall`` is applied strictly (as with ``icforge lint``): a lint warning aborts
  the build instead of being silently tolerated.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .evidence import hdl_input_hashes, sha
from .project import load_manifest
from .yosys import absolute_sources
from .terminal import write_json

SVSIM_VERSION = 1
DEFAULT_CFLAGS = "-O2"


def resolve_svsim_plan(manifest: dict, *, top: str | None = None, sources: list[str] | None = None,
                       cflags: str | None = None) -> dict:
    """Resolve the class-environment intent from arguments or the manifest ``svsim`` block."""
    section = manifest.get("svsim") if isinstance(manifest.get("svsim"), dict) else {}
    resolved_top = top or section.get("top")
    if not isinstance(resolved_top, str) or not resolved_top.strip():
        raise ValueError("svsim requires an environment top module (manifest svsim.top)")
    resolved_sources = sources if sources is not None else section.get("sources")
    if not isinstance(resolved_sources, list) or not resolved_sources:
        raise ValueError("svsim requires a nonempty source list (manifest svsim.sources)")
    resolved_cflags = cflags if cflags is not None else section.get("cflags") or DEFAULT_CFLAGS
    if not isinstance(resolved_cflags, str):
        raise ValueError("svsim cflags must be a string")
    return {"top": resolved_top, "sources": list(resolved_sources), "cflags": resolved_cflags}


def _svsim_inputs(project: Path, plan: dict) -> dict:
    result = hdl_input_hashes(project, plan["sources"])
    for name in ("spec/brief.md", "spec/design.md", "spec/verification-plan.json"):
        path = project / name
        if path.is_file():
            result[name] = sha(path)
    result["@checker"] = sha(Path(__file__))
    config = {"top": plan["top"], "sources": plan["sources"], "cflags": plan["cflags"],
              "version": SVSIM_VERSION}
    result["@svsim_config"] = hashlib.sha256(json.dumps(config, sort_keys=True).encode("utf-8")).hexdigest()
    return result


def _verilator_root(command: list[str]) -> Path | None:
    override = os.environ.get("ICFORGE_VERILATOR_ROOT")
    if override:
        candidate = Path(override).expanduser()
        if candidate.is_dir():
            return candidate
    for item in command:
        path = Path(item)
        if path.name.lower() not in {"verilator", "verilator.exe", "verilator_bin", "verilator_bin.exe"}:
            continue
        resolved = path.resolve()
        candidate = resolved.parent.parent / "share" / "verilator"
        if candidate.is_dir():
            return candidate
    return None


def _resolve_toolchain() -> dict:
    """Resolve Verilator plus the C++/make tools ``--binary`` needs, with an environment."""
    from .quality import _verilator_command

    command = _verilator_command()
    tool = {"command": command, "version": None, "cxx": None, "make": None,
            "env": dict(os.environ), "reason": None}
    if not command:
        tool["reason"] = "Verilator is not installed or not on PATH."
        return tool
    env = tool["env"]
    root = _verilator_root(command)
    if root is not None:
        # ``--binary`` feeds VERILATOR_ROOT into a generated makefile that joins
        # paths with ``/``; a Windows-style backslash root produces a broken
        # ``verilator_includer`` path, so always hand over a POSIX path.
        env["VERILATOR_ROOT"] = root.as_posix()
    for item in command:
        resolved = Path(item).resolve()
        subsystem = resolved.parent.parent
        if subsystem.name.lower() in {"ucrt64", "mingw64", "clang64", "clangarm64"}:
            # MSYS2 keeps the helpers Verilator's makefile needs (uname, python3,
            # ar) in ``usr/bin``; the compiler lives in the subsystem's bin.
            usr_bin = subsystem.parent / "usr" / "bin"
            if usr_bin.is_dir():
                env["PATH"] = os.pathsep.join(
                    [str(usr_bin), str(subsystem / "bin"), env.get("PATH", "")])
            break
    try:
        probe = subprocess.run(command + ["--version"], capture_output=True, text=True,
                               timeout=30, check=False, env=env)
        text = (probe.stdout + probe.stderr).strip()
        tool["version"] = text.splitlines()[0].strip() if text else None
    except (OSError, subprocess.TimeoutExpired):
        pass
    tool["cxx"] = (shutil.which("g++", path=env.get("PATH")) or shutil.which("c++", path=env.get("PATH"))
                   or shutil.which("clang++", path=env.get("PATH")))
    make = shutil.which("make", path=env.get("PATH"))
    if make:
        tool["make"] = make
    else:
        mingw = shutil.which("mingw32-make", path=env.get("PATH"))
        if mingw:
            tool["make"] = mingw
            env["MAKE"] = "mingw32-make"
    if not tool["cxx"]:
        tool["reason"] = "No C++ compiler (g++/clang++) was found for Verilator --binary."
    elif not tool["make"]:
        tool["reason"] = "No make program was found for Verilator --binary."
    return tool


def _plan_requirements(project: Path) -> tuple[list[str] | None, str | None]:
    plan_path = project / "spec/verification-plan.json"
    if not plan_path.is_file():
        return None, "spec/verification-plan.json is missing"
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except ValueError:
        return None, "spec/verification-plan.json is not valid JSON"
    requirements = plan.get("requirements") if isinstance(plan, dict) else None
    if not isinstance(requirements, list) or not requirements:
        return None, "verification plan has no requirements"
    identifiers = [item.get("id") if isinstance(item, dict) else None for item in requirements]
    if (any(not isinstance(i, str) or not re.fullmatch(r"REQ-[0-9]{3,}", i) for i in identifiers)
            or len(set(identifiers)) != len(identifiers)):
        return None, "verification plan requirement IDs are invalid or duplicated"
    return identifiers, None


def run_svsim(project: Path, *, top: str | None = None, sources: list[str] | None = None,
              cflags: str | None = None, timeout: int = 300,
              progress: Callable[[str], None] | None = None) -> dict:
    """Build and run a class-based environment with Verilator and record evidence."""
    if type(timeout) is not int or not 1 <= timeout <= 86400:
        raise ValueError("timeout must be 1..86400 seconds")
    project = project.resolve()
    manifest = load_manifest(project)
    plan = resolve_svsim_plan(manifest, top=top, sources=sources, cflags=cflags)
    plan["sources"] = absolute_sources(project, plan["sources"], label="svsim sources")
    inputs = _svsim_inputs(project, plan)
    started_at = datetime.now(timezone.utc).isoformat()
    tool = _resolve_toolchain()
    obj_rel = Path("build") / "svsim" / "obj"
    executable = obj_rel / ("sim.exe" if os.name == "nt" else "sim")
    report: dict = {
        "schema_version": 1, "svsim_version": SVSIM_VERSION, "kind": "svsim",
        "project": manifest.get("project", project.name), "status": "blocked",
        "top": plan["top"], "sources": plan["sources"], "cflags": plan["cflags"],
        "tool": "Verilator", "tool_version": tool["version"], "cxx": tool["cxx"],
        "make": tool["make"], "command": [], "obj_dir": obj_rel.as_posix(),
        "executable": executable.as_posix(), "inputs": inputs, "checks": [],
        "min_checks": None, "returncode": None, "started_at": started_at,
        "elapsed_seconds": 0.0, "reason": tool["reason"] or "Verilator is not available.",
        "output_tail": "",
    }
    if not tool["command"] or tool["reason"]:
        if progress:
            progress(f"Class-environment simulation blocked: {report['reason']}")
        write_json(project / "reports/svsim.json", report)
        (project / "reports/svsim.log").write_text(report["reason"] + "\n", encoding="utf-8")
        return report

    obj_dir = project / obj_rel
    obj_dir.mkdir(parents=True, exist_ok=True)
    command = tool["command"] + [
        "--binary", "--timing", "-Wall", "--top-module", plan["top"],
        "-Mdir", str(obj_dir), "-o", "sim", "-CFLAGS", plan["cflags"],
        "-I" + str(project), *[str(project / name) for name in plan["sources"]],
    ]
    report["command"] = command
    if progress:
        progress(f"Building class environment {plan['top']} via {tool['version'] or 'Verilator'}.")
    start_clock = time.monotonic()
    build_returncode, build_output = _run(command, project, tool["env"], timeout)
    build_elapsed = time.monotonic() - start_clock
    report["output_tail"] = build_output[-4000:]
    exe_path = project / executable
    if build_returncode == 124:
        report["status"] = "error"
        report["reason"] = f"Verilator build timed out after {timeout}s."
    elif build_returncode not in (0, None) or not exe_path.is_file():
        report["status"] = "error"
        report["reason"] = _first_error(build_output) or "Verilator failed to build the environment."
    else:
        if progress:
            progress(f"Running class environment {executable.name}.")
        run_clock = time.monotonic()
        run_returncode, run_output = _run([str(exe_path)], project, tool["env"], timeout)
        report["elapsed_seconds"] = round(build_elapsed + (time.monotonic() - run_clock), 3)
        report["returncode"] = run_returncode
        report["output_tail"] = (build_output + run_output)[-4000:]
        report["checks"] = _evaluate_runtime(project, run_returncode, run_output, report)
        _finalize_status(report, run_returncode)
        write_json(project / "reports/svsim.json", report)
        (project / "reports/svsim.log").write_text(build_output + run_output, encoding="utf-8")
        if progress:
            progress(f"Class-environment simulation {report['status']}: {report['reason']}")
        return report
    report["elapsed_seconds"] = round(build_elapsed, 3)
    write_json(project / "reports/svsim.json", report)
    (project / "reports/svsim.log").write_text(build_output, encoding="utf-8")
    if progress:
        progress(f"Class-environment simulation {report['status']}: {report['reason']}")
    return report


def _run(command: list[str], project: Path, env: dict, timeout: int) -> tuple[int | None, str]:
    try:
        result = subprocess.run(command, cwd=project, env=env, capture_output=True, text=True,
                                timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        text = (exc.stdout or "") + (exc.stderr or "")
        if isinstance(text, bytes):
            text = text.decode("utf-8", errors="replace")
        return 124, text + f"\nCommand timed out after {timeout}s.\n"
    except OSError as exc:
        return None, f"{type(exc).__name__}: {exc}"
    return result.returncode, result.stdout + result.stderr


def _first_error(output: str) -> str:
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("%Error"):
            return stripped[:300]
    return ""


def _evaluate_runtime(project: Path, returncode: int | None, runtime_output: str, report: dict) -> list[dict]:
    identifiers, plan_reason = _plan_requirements(project)
    if identifiers is None:
        report["plan_error"] = plan_reason
        return []
    markers = re.findall(r"(?m)^\s*CHECK\s+(\S+)\s+PASS\s*$", runtime_output)
    checks = [{"id": identifier,
               "status": "pass" if markers.count(identifier) == 1 else "fail"}
              for identifier in identifiers]
    report["min_checks"] = len(identifiers)
    report["plan_error"] = (None if set(markers) == set(identifiers)
                            else "CHECK markers do not match the verification plan")
    return checks


def _finalize_status(report: dict, returncode: int | None) -> None:
    runtime_tail = report["output_tail"]
    failed = bool(re.search(r"(?im)^\s*(?:FAIL|FATAL|ERROR)(?:\b|:)", runtime_tail))
    passed_marker = bool(re.search(r"(?m)^\s*PASS(?:\b|:)", runtime_tail))
    checks = report["checks"]
    if returncode == 124:
        report["status"] = "fail"
        report["reason"] = "Class-environment simulation timed out."
    elif returncode not in (0, None):
        report["status"] = "fail"
        report["reason"] = f"Environment exited with return code {returncode}."
    elif report.get("plan_error"):
        report["status"] = "fail"
        report["reason"] = str(report["plan_error"])
    elif failed or not passed_marker:
        report["status"] = "fail"
        report["reason"] = "Environment did not report a clean pass line."
    elif checks and any(item["status"] != "pass" for item in checks):
        report["status"] = "fail"
        report["reason"] = "One or more planned requirement checks are missing or duplicated."
    else:
        report["status"] = "pass"
        report["reason"] = f"Class environment passed {len(checks)} planned requirement checks."


def svsim_valid(project: Path, manifest: dict, report: dict) -> tuple[bool, str]:
    if not isinstance(report, dict):
        return False, "Class-environment report is not an object"
    if report.get("kind") != "svsim":
        return False, "Class-environment report kind mismatch"
    if report.get("status") != "pass":
        return False, f"Class-environment simulation has not passed ({report.get('status')})"
    try:
        plan = resolve_svsim_plan(manifest, top=report.get("top"), sources=report.get("sources"),
                                  cflags=report.get("cflags"))
        plan["sources"] = absolute_sources(project, plan["sources"], label="svsim sources")
        expected = _svsim_inputs(project, plan)
    except (OSError, ValueError, KeyError) as exc:
        return False, str(exc)
    if report.get("inputs") != expected:
        return False, "RTL or verification sources changed since the class-environment run"
    return True, "Current class-based environment execution passed"
