"""Open-source formal property checks and logic equivalence (LEC) gates.

The backend is a native ``yosys`` on ``PATH`` (or ``ICFORGE_YOSYS``) or the
portable ``yowasp-yosys`` Python entry point. When no backend is available the
gate reports ``blocked`` instead of pretending success.

Honesty contract (see ``docs/formal.md``):

* A ``sat -seq <depth> -prove-asserts`` pass only covers ``depth`` clock cycles and
  is recorded with ``bounded: true``; it is never reported as a proof. The clock
  becomes a plain signal under ``clk2fflogic``, so the script requests
  ``2 * depth + 2`` SAT steps to guarantee ``depth`` cycles (``steps`` is recorded).
* A ``sat -tempinduct`` pass records an unbounded sequential proof.
* A LEC pass records the exact proven/unproven equivalence-cell counts; a partial
  proof never reads as "equivalent".
* A design that declares no assertions yields ``not_applicable``, never ``pass``.
* ``set_init_zero`` (on by default) assumes every flip-flop powers up at zero, the
  FPGA vendor default, and is applied with ``setundef -init -zero`` *before*
  ``clk2fflogic``. The ``sat -set-init-zero`` flag must never be combined with
  ``clk2fflogic``: it also zeroes the clock-emulation flip-flops and silently
  masks counterexamples. Disable the assumption to check from a fully
  unconstrained initial state.
"""
from __future__ import annotations

import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .evidence import hdl_input_hashes, sha
from .project import load_manifest
from .terminal import redact
from .yosys import (
    absolute_sources as _absolute,
    checker_hash,
    config_hash as _config_hash,
    first_error as _first_error,
    read_files as _read_files,
    run_script as _run,
    tool_fingerprint,
    tool_version,
    write_report as _write_report,
    write_script,
    yosys_command,
)

LEC_VERSION = 1
FORMAL_VERSION = 1

# Yosys normalization applied identically to both LEC sides before comparison.
# ``async2sync`` lowers asynchronous resets to a conservative synchronous model so
# SAT can reason about the equivalence points; ``techmap`` lowers the netlist side
# to generic gates. Both sides share the same reset model, so the comparison stays
# sound under that model.
LEC_NORMALIZATION = ("proc", "opt", "memory", "opt", "async2sync")


def _checker_hash() -> str:
    return checker_hash(Path(__file__))


def _write_script(project: Path, name: str, text: str) -> str:
    return write_script(project, "formal", name, text)


def resolve_lec_plan(manifest: dict, *, mode: str | None = None, top: str | None = None,
                     gold: list[str] | None = None, gate: list[str] | None = None) -> dict:
    """Resolve the LEC intent from explicit arguments or the manifest ``lec`` block."""
    section = manifest.get("lec") if isinstance(manifest.get("lec"), dict) else {}
    resolved_top = top or section.get("top") or manifest.get("top_module")
    if not isinstance(resolved_top, str) or not resolved_top.strip():
        raise ValueError("LEC requires a top module")
    gate_sources = gate if gate is not None else section.get("gate_sources")
    resolved_mode = mode or section.get("mode") or ("designs" if gate_sources else "synth")
    if resolved_mode not in {"synth", "designs"}:
        raise ValueError("LEC mode must be 'synth' or 'designs'")
    gold_sources = gold if gold is not None else section.get("gold_sources") or manifest.get("rtl_sources")
    if resolved_mode == "designs" and not gate_sources:
        raise ValueError("LEC mode 'designs' needs gate sources (manifest lec.gate_sources or --gate)")
    return {"mode": resolved_mode, "top": resolved_top,
            "gold_sources": list(gold_sources or []),
            "gate_sources": list(gate_sources) if gate_sources else []}


def _lec_inputs(project: Path, plan: dict) -> dict:
    files = list(plan["gold_sources"]) + list(plan["gate_sources"])
    result = hdl_input_hashes(project, files)
    result["@checker"] = _checker_hash()
    result["@lec_config"] = _config_hash({"mode": plan["mode"], "top": plan["top"],
                                          "gold": plan["gold_sources"], "gate": plan["gate_sources"],
                                          "normalization": list(LEC_NORMALIZATION), "version": LEC_VERSION})
    return result


def _lec_script(plan: dict, *, netlist: str) -> str:
    gold = _read_files(plan["gold_sources"])
    gate = _read_files(plan["gate_sources"] or plan["gold_sources"])
    top = plan["top"]
    lines = [
        f"read_verilog -sv {gold}",
        f"hierarchy -top {top}",
        *LEC_NORMALIZATION,
        "design -stash gold",
        f"read_verilog -sv {gate}",
        f"hierarchy -top {top}",
        *LEC_NORMALIZATION,
        "techmap",
        "opt",
        f"write_verilog -noattr {netlist}",
        "design -stash gate",
        f"design -copy-from gold -as gold {top}",
        f"design -copy-from gate -as gate {top}",
        "equiv_make gold gate equiv",
        "hierarchy -top equiv",
        "equiv_simple",
        "equiv_induct",
        "equiv_status -assert",
    ]
    return "\n".join(lines) + "\n"


_LEC_TALLY = re.compile(r"Of those cells (\d+) are proven and (\d+) are unproven")


def _parse_lec(output: str) -> dict:
    proven = unproven = 0
    points: list[str] = []
    for line in output.splitlines():
        stripped = line.strip()
        tally = _LEC_TALLY.search(stripped)
        if tally:
            proven, unproven = int(tally.group(1)), int(tally.group(2))
        if stripped.startswith("Unproven $equiv") and ": \\" in stripped:
            points.append(stripped.split(": \\", 1)[1].strip())
    return {"proven": proven, "unproven": unproven, "unproven_points": points,
            "equivalent": "Equivalence successfully proven!" in output}


def run_lec(project: Path, *, mode: str | None = None, top: str | None = None,
            gold: list[str] | None = None, gate: list[str] | None = None,
            timeout: int = 300, progress: Callable[[str], None] | None = None) -> dict:
    """Prove logic equivalence between a reference design and an implementation.

    ``synth`` mode (default) proves the project RTL equals its own
    technology-independent gate netlist, and writes that netlist under
    ``build/lec/``. ``designs`` mode proves two declared HDL source sets
    equivalent (gold reference vs. revised implementation).
    """
    if type(timeout) is not int or not 1 <= timeout <= 86400:
        raise ValueError("timeout must be 1..86400 seconds")
    project = project.resolve()
    manifest = load_manifest(project)
    plan = resolve_lec_plan(manifest, mode=mode, top=top, gold=gold, gate=gate)
    plan["gold_sources"] = _absolute(project, plan["gold_sources"], label="gold_sources")
    if plan["gate_sources"]:
        plan["gate_sources"] = _absolute(project, plan["gate_sources"], label="gate_sources")
    inputs = _lec_inputs(project, plan)
    netlist_rel = Path("build") / "lec" / f"{plan['top']}_netlist.v"
    (project / netlist_rel).parent.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(timezone.utc).isoformat()
    command = yosys_command()
    report: dict = {
        "schema_version": 1, "lec_version": LEC_VERSION, "kind": "lec",
        "project": manifest.get("project", project.name), "status": "blocked",
        "mode": plan["mode"], "top": plan["top"], "sequential": True,
        "normalization": ",".join(LEC_NORMALIZATION) + ",techmap",
        "tool": "Yosys", "tool_version": None, "command": [], "script": None,
        "netlist": None, "proven": 0, "unproven": 0, "unproven_points": [],
        "started_at": started_at, "elapsed_seconds": 0.0, "inputs": inputs,
        "gold_sources": plan["gold_sources"], "gate_sources": plan["gate_sources"],
        "reason": "Yosys is not installed or not on PATH.", "output_tail": "",
    }
    if not command:
        if progress:
            progress("LEC blocked: Yosys is not available.")
        _write_report(project, "lec", report, "")
        return report
    report["tool_version"] = tool_version(command)
    script_rel = _write_script(project, "lec.ys", _lec_script(plan, netlist=netlist_rel.as_posix()))
    report["script"] = script_rel
    report["command"] = command + [script_rel]
    if progress:
        progress(f"LEC ({plan['mode']}) on {plan['top']} via {report['tool_version'] or 'Yosys'}.")
    start_clock = time.monotonic()
    returncode, output = _run(project, report["command"], timeout)
    report["elapsed_seconds"] = round(time.monotonic() - start_clock, 3)
    parsed = _parse_lec(output)
    report.update(proven=parsed["proven"], unproven=parsed["unproven"],
                  unproven_points=parsed["unproven_points"])
    netlist_path = project / netlist_rel
    if netlist_path.is_file() and netlist_path.stat().st_size > 0:
        report["netlist"] = {"path": netlist_rel.as_posix(), "sha256": sha(netlist_path)}
    if parsed["equivalent"] and returncode == 0:
        report["status"] = "pass"
        report["reason"] = f"Logic equivalence proven ({parsed['proven']} equivalence points)."
    elif "unproven $equiv cells" in output:
        report["status"] = "fail"
        report["reason"] = f"{parsed['unproven']} equivalence cells could not be proven."
    elif "ERROR:" in output or returncode not in (0, None):
        report["status"] = "error"
        report["reason"] = _first_error(output)
    else:
        report["status"] = "error"
        report["reason"] = "LEC produced no equivalence verdict."
    report["output_tail"] = redact(output[-4000:], dict(os.environ))
    _write_report(project, "lec", report, output)
    if progress:
        progress(f"LEC {report['status']}: {report['proven']} proven, {report['unproven']} unproven.")
    return report


def lec_valid(project: Path, manifest: dict, report: dict) -> tuple[bool, str]:
    if not isinstance(report, dict):
        return False, "LEC report is not an object"
    if report.get("kind") != "lec":
        return False, "LEC report kind mismatch"
    if report.get("status") != "pass":
        return False, f"LEC has not passed ({report.get('status')})"
    try:
        plan = resolve_lec_plan(manifest, mode=report.get("mode"), top=report.get("top"),
                                gold=report.get("gold_sources"), gate=report.get("gate_sources"))
        plan["gold_sources"] = _absolute(project, plan["gold_sources"], label="gold_sources")
        if plan["gate_sources"]:
            plan["gate_sources"] = _absolute(project, plan["gate_sources"], label="gate_sources")
        expected = _lec_inputs(project, plan)
    except (OSError, ValueError, KeyError) as exc:
        return False, str(exc)
    if report.get("inputs") != expected:
        return False, "RTL changed since the LEC proof"
    if type(report.get("unproven")) is not int or report["unproven"] != 0:
        return False, "LEC report contains unproven equivalence points"
    if type(report.get("proven")) is not int or report["proven"] < 1:
        return False, "LEC report proved no equivalence points"
    return True, "gate-level equivalence proven on the current inputs"


def resolve_formal_plan(manifest: dict, *, top: str | None = None, depth: int | None = None,
                        mode: str | None = None, set_init_zero: bool | None = None) -> dict:
    section = manifest.get("formal") if isinstance(manifest.get("formal"), dict) else {}
    resolved_top = top or section.get("top") or manifest.get("top_module")
    if not isinstance(resolved_top, str) or not resolved_top.strip():
        raise ValueError("Formal check requires a top module")
    sources = section.get("sources") or manifest.get("rtl_sources")
    resolved_mode = mode or section.get("mode") or "bmc"
    if resolved_mode not in {"bmc", "prove"}:
        raise ValueError("Formal mode must be 'bmc' or 'prove'")
    resolved_depth = depth if depth is not None else section.get("depth", 10)
    if type(resolved_depth) is not int or not 1 <= resolved_depth <= 100000:
        raise ValueError("Formal depth must be 1..100000 clock cycles")
    init_zero = set_init_zero if set_init_zero is not None else bool(section.get("set_init_zero", True))
    return {"top": resolved_top, "sources": list(sources or []), "depth": resolved_depth,
            "mode": resolved_mode, "set_init_zero": init_zero}


def _formal_inputs(project: Path, plan: dict) -> dict:
    result = hdl_input_hashes(project, plan["sources"])
    result["@checker"] = _checker_hash()
    result["@formal_config"] = _config_hash({key: plan[key] for key in
                                             ("top", "mode", "depth", "set_init_zero", "sources")}
                                            | {"version": FORMAL_VERSION})
    return result


def _formal_script(plan: dict) -> str:
    lines = [
        f"read_verilog -formal -sv {_read_files(plan['sources'])}",
        f"hierarchy -top {plan['top']}",
        "proc",
        "opt",
        # ``sat`` has no model for ``$memwr_v2``/``$memrd`` cells, so inferred
        # arrays must be lowered to flip-flops and logic before the check.
        "memory",
        "opt",
    ]
    if plan["set_init_zero"]:
        # FPGA power-up convention: all registers initialize to zero. Applied
        # before ``clk2fflogic`` because the ``sat -set-init-zero`` flag would
        # also zero the clock-emulation flip-flops and mask counterexamples.
        lines.append("setundef -init -zero")
    lines += [
        "clk2fflogic",
        "chformal -lower",
        "select -count t:$assert",
    ]
    sat = ["sat"]
    if plan["mode"] == "prove":
        sat.append("-tempinduct")
    else:
        # ``clk2fflogic`` turns the clock into a plain signal, so a ``sat`` step
        # is not a clock cycle: one rising edge costs roughly two steps plus a
        # fixed startup. ``2 * depth + 2`` steps therefore guarantee that at
        # least ``depth`` clock cycles are explored, keeping ``--depth``
        # meaningful as a clock-cycle bound.
        sat += ["-seq", str(2 * plan["depth"] + 2)]
    sat += ["-prove-asserts", "-verify"]
    lines.append(" ".join(sat))
    return "\n".join(lines) + "\n"


_OBJECTS_COUNT = re.compile(r"^(\d+)\s+objects\.$")


def _parse_formal(output: str, mode: str) -> dict:
    assertions = None
    for line in output.splitlines():
        match = _OBJECTS_COUNT.match(line.strip())
        if match:
            assertions = int(match.group(1))
    success = ("no model found: SUCCESS!" in output
               or "Induction step proven: SUCCESS!" in output)
    failure = ("model found: FAIL!" in output
               or "model found for base case: FAIL!" in output)
    return {"assertions": assertions, "success": success, "failure": failure}


def run_formal(project: Path, *, top: str | None = None, depth: int | None = None,
               mode: str | None = None, set_init_zero: bool | None = None,
               timeout: int = 300, progress: Callable[[str], None] | None = None) -> dict:
    """Run bounded (``bmc``) or unbounded temporal-induction (``prove``) property checks.

    The check uses Yosys ``sat`` directly, so no SymbiYosys install is required.
    A pass with ``mode == 'bmc'`` only covers ``depth`` cycles and is reported as
    bounded; ``prove`` reports an unbounded sequential proof.
    """
    if type(timeout) is not int or not 1 <= timeout <= 86400:
        raise ValueError("timeout must be 1..86400 seconds")
    project = project.resolve()
    manifest = load_manifest(project)
    plan = resolve_formal_plan(manifest, top=top, depth=depth, mode=mode, set_init_zero=set_init_zero)
    plan["sources"] = _absolute(project, plan["sources"], label="formal sources")
    inputs = _formal_inputs(project, plan)
    started_at = datetime.now(timezone.utc).isoformat()
    command = yosys_command()
    report: dict = {
        "schema_version": 1, "formal_version": FORMAL_VERSION, "kind": "formal",
        "project": manifest.get("project", project.name), "status": "blocked",
        "mode": plan["mode"], "bounded": plan["mode"] == "bmc", "depth": plan["depth"],
        "steps": None if plan["mode"] == "prove" else 2 * plan["depth"] + 2,
        "set_init_zero": plan["set_init_zero"], "assertions": None, "top": plan["top"],
        "sources": plan["sources"], "tool": "Yosys", "tool_version": None,
        "command": [], "script": None, "started_at": started_at, "elapsed_seconds": 0.0,
        "inputs": inputs, "reason": "Yosys is not installed or not on PATH.", "output_tail": "",
    }
    if not command:
        if progress:
            progress("Formal check blocked: Yosys is not available.")
        _write_report(project, "formal", report, "")
        return report
    report["tool_version"] = tool_version(command)
    script_rel = _write_script(project, "formal.ys", _formal_script(plan))
    report["script"] = script_rel
    report["command"] = command + [script_rel]
    if progress:
        suffix = "" if plan["mode"] != "bmc" else ", depth=" + str(plan["depth"])
        progress(f"Formal ({plan['mode']}{suffix}) "
                 f"on {plan['top']} via {report['tool_version'] or 'Yosys'}.")
    start_clock = time.monotonic()
    returncode, output = _run(project, report["command"], timeout)
    report["elapsed_seconds"] = round(time.monotonic() - start_clock, 3)
    parsed = _parse_formal(output, plan["mode"])
    report["assertions"] = parsed["assertions"]
    if returncode == 124:
        report["status"] = "error"
        report["reason"] = f"Formal check timed out after {timeout}s."
    elif parsed["assertions"] == 0:
        report["status"] = "not_applicable"
        report["reason"] = ("The design declares no assertions or covers; "
                            "add immediate/concurrent properties to the RTL to gain formal value.")
    elif parsed["failure"]:
        report["status"] = "fail"
        report["reason"] = "A counterexample falsifies a property."
    elif parsed["success"] and returncode == 0:
        report["status"] = "pass"
        report["reason"] = ("Properties proven by temporal induction (unbounded)."
                            if plan["mode"] == "prove" else
                            f"Properties hold for at least {plan['depth']} clock cycles (bounded).")
    elif "ERROR:" in output:
        report["status"] = "error"
        report["reason"] = _first_error(output)
    else:
        report["status"] = "error"
        report["reason"] = "Formal check produced no verdict."
    report["output_tail"] = redact(output[-4000:], dict(os.environ))
    _write_report(project, "formal", report, output)
    if progress:
        progress(f"Formal {report['status']}: {report['reason']}")
    return report


def formal_valid(project: Path, manifest: dict, report: dict) -> tuple[bool, str]:
    if not isinstance(report, dict):
        return False, "Formal report is not an object"
    if report.get("kind") != "formal":
        return False, "Formal report kind mismatch"
    if report.get("status") != "pass":
        return False, f"Formal properties have not passed ({report.get('status')})"
    try:
        plan = resolve_formal_plan(manifest, top=report.get("top"), depth=report.get("depth"),
                                   mode=report.get("mode"), set_init_zero=report.get("set_init_zero"))
        plan["sources"] = _absolute(project, plan["sources"], label="formal sources")
        expected = _formal_inputs(project, plan)
    except (OSError, ValueError, KeyError) as exc:
        return False, str(exc)
    if report.get("inputs") != expected:
        return False, "RTL changed since the formal proof"
    if type(report.get("assertions")) is not int or report["assertions"] < 1:
        return False, "Formal proof covered no assertions"
    return True, "declared properties proven on the current inputs"
