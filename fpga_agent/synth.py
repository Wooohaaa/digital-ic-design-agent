"""Vendor-neutral RTL-to-netlist synthesis evidence.

This gate lowers a project's RTL to a technology-independent gate netlist with
Yosys, without any vendor library, place-and-route tool or license, so it runs
on any Windows/Linux host that has Yosys. It answers two questions with hashed
evidence: *does the RTL synthesize?* and *what generic cell/flip-flop resources
does it need?* The result is deliberately vendor-neutral, so it is not tied to
the Anlogic TD install the way :mod:`fpga_agent.td` is.

Honesty contract (see ``docs/synth.md``):

* The artifact is a technology-independent netlist (generic gates plus flip-flop
  cells), never a vendor-mapped or placed/routed design and never a bitstream.
* Cell/flip-flop counts are a resource estimate from that generic netlist; they
  are not area, timing, power or a vendor resource report.
* Editing any declared source invalidates the recorded netlist hash, so the gate
  never reads a stale netlist as current.
* When Yosys is unavailable the gate reports ``blocked`` instead of pretending
  success.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .evidence import hdl_input_hashes, sha
from .project import load_manifest
from .yosys import (
    absolute_sources,
    checker_hash,
    config_hash,
    first_error,
    read_files,
    run_script,
    tool_version,
    write_report,
    write_script,
    yosys_command,
)

SYNTH_VERSION = 1


def _is_flop(cell_type: str) -> bool:
    name = cell_type.upper()
    core = name.lstrip("$").lstrip("_")
    return (core.startswith(("DFF", "SDFF", "ADFF", "DLATCH", "LATCH", "SR"))
            or name in {"$FF", "$DFF", "$DFFE", "$SDFF", "$ADFF", "$DLATCH"})


def resolve_synth_plan(manifest: dict, *, top: str | None = None,
                       flatten: bool | None = None, sources: list[str] | None = None) -> dict:
    """Resolve the synthesis intent from explicit arguments or the manifest ``synth`` block."""
    section = manifest.get("synth") if isinstance(manifest.get("synth"), dict) else {}
    resolved_top = top or section.get("top") or manifest.get("top_module")
    if not isinstance(resolved_top, str) or not resolved_top.strip():
        raise ValueError("Synthesis requires a top module")
    resolved_sources = sources if sources is not None else section.get("sources") or manifest.get("rtl_sources")
    resolved_flatten = flatten if flatten is not None else bool(section.get("flatten", True))
    return {"top": resolved_top, "sources": list(resolved_sources or []), "flatten": resolved_flatten}


def _synth_inputs(project: Path, plan: dict) -> dict:
    result = hdl_input_hashes(project, plan["sources"])
    result["@checker"] = checker_hash(Path(__file__))
    result["@synth_config"] = config_hash({"top": plan["top"], "flatten": plan["flatten"],
                                           "sources": plan["sources"], "version": SYNTH_VERSION})
    return result


def _synth_script(plan: dict, *, netlist: str, stat: str) -> str:
    lines = [
        f"read_verilog -sv {read_files(plan['sources'])}",
        f"hierarchy -top {plan['top']}",
        "proc",
        "opt",
        # ``sat``/netlist consumers cannot model ``$mem`` cells, and a netlist
        # should contain only real logic, so lower inferred arrays and drop the
        # formal-only ``$check``/``$assume`` cells the parser emits.
        "memory",
        "opt",
        "chformal -remove",
    ]
    if plan["flatten"]:
        lines.append("flatten")
    lines += [
        # ``techmap`` lowers coarse RTL cells to technology-independent gates via
        # Yosys' built-in simplemap rules. ABC is intentionally not used: it is
        # unstable under the portable WASM build on async-reset designs.
        "techmap",
        "opt",
        f"tee -q -o {stat} stat -json",
        f"write_verilog -noattr {netlist}",
    ]
    return "\n".join(lines) + "\n"


def _stat_block(text: str) -> dict | None:
    """Extract the JSON object printed by ``stat -json`` from a Yosys log."""
    lines = text.splitlines()
    start = next((index for index, line in enumerate(lines) if line.strip() == "{"), None)
    if start is None:
        return None
    depth = 0
    end = None
    for index in range(start, len(lines)):
        depth += lines[index].count("{") - lines[index].count("}")
        if depth == 0:
            end = index
            break
    if end is None:
        return None
    try:
        return json.loads("\n".join(lines[start:end + 1]))
    except ValueError:
        return None


def _cell_summary(data: dict | None, top: str) -> tuple[int | None, dict[str, int], int | None]:
    if not isinstance(data, dict):
        return None, {}, None
    module = None
    modules = data.get("modules")
    if isinstance(modules, dict):
        module = modules.get(f"\\{top}") or modules.get(top)
        if module is None and len(modules) == 1:
            module = next(iter(modules.values()))
    if not isinstance(module, dict):
        module = data.get("design") if isinstance(data.get("design"), dict) else None
    if not isinstance(module, dict):
        return None, {}, None
    by_type = module.get("num_cells_by_type")
    by_type = {str(name): int(count) for name, count in by_type.items()
               if isinstance(count, int)} if isinstance(by_type, dict) else {}
    cells = module.get("num_cells") if isinstance(module.get("num_cells"), int) else (
        sum(by_type.values()) if by_type else None)
    flops = sum(count for name, count in by_type.items() if _is_flop(name)) if by_type else None
    return cells, by_type, flops


def run_synth(project: Path, *, top: str | None = None, flatten: bool | None = None,
              timeout: int = 300, progress: Callable[[str], None] | None = None) -> dict:
    """Synthesize project RTL to a technology-independent netlist and record evidence."""
    if type(timeout) is not int or not 1 <= timeout <= 86400:
        raise ValueError("timeout must be 1..86400 seconds")
    project = project.resolve()
    manifest = load_manifest(project)
    plan = resolve_synth_plan(manifest, top=top, flatten=flatten)
    plan["sources"] = absolute_sources(project, plan["sources"], label="synthesis sources")
    inputs = _synth_inputs(project, plan)
    netlist_rel = Path("build") / "synth" / f"{plan['top']}_synth.v"
    stat_rel = Path("build") / "synth" / f"{plan['top']}_stat.json"
    (project / netlist_rel).parent.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(timezone.utc).isoformat()
    command = yosys_command()
    report: dict = {
        "schema_version": 1, "synth_version": SYNTH_VERSION, "kind": "synth",
        "project": manifest.get("project", project.name), "status": "blocked",
        "top": plan["top"], "flatten": plan["flatten"], "sources": plan["sources"],
        "tool": "Yosys", "tool_version": None, "command": [], "script": None,
        "netlist": None, "cells": None, "flops": None, "cell_types": {},
        "started_at": started_at, "elapsed_seconds": 0.0, "inputs": inputs,
        "reason": "Yosys is not installed or not on PATH.", "output_tail": "",
    }
    if not command:
        if progress:
            progress("Synthesis blocked: Yosys is not available.")
        write_report(project, "synth", report, "")
        return report
    report["tool_version"] = tool_version(command)
    script_rel = write_script(project, "synth", "synth.ys",
                              _synth_script(plan, netlist=netlist_rel.as_posix(),
                                            stat=stat_rel.as_posix()))
    report["script"] = script_rel
    report["command"] = command + [script_rel]
    if progress:
        progress(f"Synthesizing {plan['top']} via {report['tool_version'] or 'Yosys'}.")
    start_clock = time.monotonic()
    returncode, output = run_script(project, report["command"], timeout)
    report["elapsed_seconds"] = round(time.monotonic() - start_clock, 3)
    stat_path = project / stat_rel
    stat_text = stat_path.read_text(encoding="utf-8", errors="replace") if stat_path.is_file() else output
    cells, by_type, flops = _cell_summary(_stat_block(stat_text), plan["top"])
    report["cells"], report["cell_types"], report["flops"] = cells, by_type, flops
    netlist_path = project / netlist_rel
    if netlist_path.is_file() and netlist_path.stat().st_size > 0:
        report["netlist"] = {"path": netlist_rel.as_posix(), "sha256": sha(netlist_path)}
    if returncode == 124:
        report["status"] = "error"
        report["reason"] = f"Synthesis timed out after {timeout}s."
    elif returncode not in (0, None):
        report["status"] = "error"
        report["reason"] = first_error(output)
    elif report["netlist"] is None:
        report["status"] = "error"
        report["reason"] = "Yosys exited without writing a netlist."
    else:
        report["status"] = "pass"
        report["reason"] = (f"RTL synthesized to a {cells}-cell generic netlist"
                            + (f" ({flops} flip-flops)." if flops is not None else "."))
    report["output_tail"] = output[-4000:]
    write_report(project, "synth", report, output)
    if progress:
        progress(f"Synthesis {report['status']}: {report['reason']}")
    return report


def synth_valid(project: Path, manifest: dict, report: dict) -> tuple[bool, str]:
    if not isinstance(report, dict):
        return False, "Synthesis report is not an object"
    if report.get("kind") != "synth":
        return False, "Synthesis report kind mismatch"
    if report.get("status") != "pass":
        return False, f"Synthesis has not passed ({report.get('status')})"
    try:
        plan = resolve_synth_plan(manifest, top=report.get("top"), flatten=report.get("flatten"),
                                  sources=report.get("sources"))
        plan["sources"] = absolute_sources(project, plan["sources"], label="synthesis sources")
        expected = _synth_inputs(project, plan)
    except (OSError, ValueError, KeyError) as exc:
        return False, str(exc)
    if report.get("inputs") != expected:
        return False, "RTL changed since synthesis"
    netlist = report.get("netlist")
    if not isinstance(netlist, dict) or not isinstance(netlist.get("path"), str):
        return False, "Synthesis netlist evidence is missing"
    path = (project / netlist["path"]).resolve()
    if project.resolve() not in path.parents or not path.is_file():
        return False, "Synthesized netlist is missing"
    if sha(path) != netlist.get("sha256"):
        return False, "Synthesized netlist was modified"
    return True, "RTL synthesized to a current generic netlist"
