"""Vendor-neutral implementation layer.

A *target* (:mod:`fpga_agent.targets`) selects one of several implementation
backends behind a single interface. The layer answers one question with hashed
evidence: *did the project reach a bitstream (or the last stage the target
offers) for the selected device?* and records it uniformly in
``reports/implement.json``.

Backends:

* ``ice40`` -- the open Lattice iCE40 flow (Yosys ``synth_ice40`` ->
  nextpnr-ice40 -> icepack), fully license-free (:mod:`fpga_agent.ice40`).
* ``anlogic`` -- the vendor Anlogic TD flow, run through the existing
  :func:`fpga_agent.cli.do_td` (single source of truth) and mapped into the
  neutral schema.
* ``generic`` -- Yosys technology-independent synthesis only; it honestly has no
  place-and-route or bitstream step (:mod:`fpga_agent.synth`).

Honesty contract (see ``docs/backends.md``):

* ``pass`` means every stage the target *offers* completed and the recorded
  artifacts still hash-match. A ``generic`` target never claims place-and-route
  or a bitstream; those stages stay ``not_run`` and the capabilities say so.
* A missing toolchain yields ``blocked``; a tool failure yields ``error``.
* Editing any RTL or constraint source invalidates the recorded hashes.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .evidence import hdl_input_hashes, sha
from .project import load_manifest
from .targets import resolve_target
from .terminal import write_json
from .yosys import absolute_sources, config_hash

IMPLEMENT_VERSION = 1

STAGE_KEYS = ("synthesis", "place_route", "bitstream")


def _stage(tool: str) -> dict:
    return {"status": "not_run", "artifact": None, "sha256": None, "tool": tool}


def _empty_stages() -> dict:
    return {"synthesis": _stage("Yosys"), "place_route": _stage(None), "bitstream": _stage(None)}


def resolve_implement_plan(manifest: dict, *, target: str | None = None, top: str | None = None,
                           sources: list[str] | None = None, stage: str | None = None) -> dict:
    """Resolve the implementation intent from arguments, the target profile and the manifest."""
    profile = resolve_target(manifest, target=target)
    section = manifest.get("implement") if isinstance(manifest.get("implement"), dict) else {}
    synth = manifest.get("synth") if isinstance(manifest.get("synth"), dict) else {}
    resolved_top = top or synth.get("top") or manifest.get("top_module")
    if not isinstance(resolved_top, str) or not resolved_top.strip():
        raise ValueError("Implementation requires a top module")
    resolved_sources = sources if sources is not None else manifest.get("rtl_sources")
    if not isinstance(resolved_sources, list) or not resolved_sources:
        raise ValueError("Implementation requires a nonempty RTL source list")
    resolved_stage = stage or section.get("stage") or ("full" if profile["backend"] != "generic" else "synth")
    if profile["backend"] == "anlogic" and resolved_stage not in {"synth", "full"}:
        raise ValueError("the anlogic backend only supports the synth or full stage")
    pins = section.get("pins") or profile["constraints"].get("pins")
    timing = section.get("timing") or profile["constraints"].get("timing")
    return {
        "target": profile,
        "backend": profile["backend"],
        "top": resolved_top,
        "sources": list(resolved_sources),
        "stage": resolved_stage,
        "pins": pins,
        "timing": timing,
        "package": profile.get("package"),
        "device": profile["device"],
        "nextpnr_device": profile.get("nextpnr_device"),
        "bitstream_extension": profile.get("bitstream_extension"),
        "capabilities": profile["capabilities"],
    }


def _implement_inputs(project: Path, plan: dict) -> dict:
    result = hdl_input_hashes(project, plan["sources"])
    for key in ("pins", "timing"):
        name = plan.get(key)
        if isinstance(name, str) and name:
            path = (project / name).resolve()
            if project.resolve() not in path.parents or not path.is_file():
                raise ValueError(f"target constraint file is missing or escapes the project: {name}")
            result[name] = sha(path)
    result["@implement_config"] = config_hash({
        "target": plan["target"]["id"], "backend": plan["backend"], "top": plan["top"],
        "sources": plan["sources"], "stage": plan["stage"], "pins": plan["pins"],
        "timing": plan["timing"], "version": IMPLEMENT_VERSION,
    })
    return result


def run_implement(project: Path, *, target: str | None = None, top: str | None = None,
                  sources: list[str] | None = None, stage: str | None = None,
                  timeout: int | None = None, td_root: str | None = None,
                  progress: Callable[[str], None] | None = None) -> dict:
    """Run the selected backend's implementation flow and record unified evidence."""
    project = project.resolve()
    manifest = load_manifest(project)
    plan = resolve_implement_plan(manifest, target=target, top=top, sources=sources, stage=stage)
    plan["sources"] = absolute_sources(project, plan["sources"], label="implementation sources")
    started_at = datetime.now(timezone.utc).isoformat()
    backend = plan["backend"]
    if timeout is None:
        timeout = 2400 if backend == "anlogic" else 600 if backend == "ice40" else 300
    if type(timeout) is not int or not 1 <= timeout <= 86400:
        raise ValueError("timeout must be 1..86400 seconds")

    report: dict = {
        "schema_version": 1, "implement_version": IMPLEMENT_VERSION, "kind": "implement",
        "project": manifest.get("project", project.name), "status": "blocked",
        "backend": backend, "stage": plan["stage"],
        "target": {key: plan["target"][key] for key in
                   ("id", "vendor", "device", "family", "board", "tool", "backend") if key in plan["target"]},
        "capabilities": plan["capabilities"],
        "top": plan["top"], "sources": plan["sources"], "constraints": {"pins": plan["pins"], "timing": plan["timing"]},
        "stages": _empty_stages(), "utilization": {}, "timing": None, "bitstream": None,
        "commands": {}, "tool_versions": {}, "inputs": {}, "started_at": started_at,
        "elapsed_seconds": 0.0, "reason": "No implementation backend was run.", "output_tail": "",
    }
    try:
        inputs = _implement_inputs(project, plan)
    except (OSError, ValueError) as exc:
        report["reason"] = str(exc)
        report["output_tail"] = report["reason"]
        write_json(project / "reports/implement.json", report)
        (project / "reports/implement.log").write_text(report["reason"] + "\n", encoding="utf-8")
        if progress:
            progress(f"Implementation blocked: {report['reason']}")
        return report
    report["inputs"] = inputs
    start_clock = time.monotonic()
    if backend == "ice40":
        body = _run_ice40(project, plan, timeout=timeout, progress=progress)
    elif backend == "anlogic":
        body = _run_anlogic(project, plan, timeout=timeout, td_root=td_root, progress=progress)
    elif backend == "generic":
        body = _run_generic(project, plan, timeout=timeout, progress=progress)
    else:  # pragma: no cover - resolve_target already guarantees a known backend
        raise ValueError(f"unsupported implementation backend: {backend}")
    report["elapsed_seconds"] = round(time.monotonic() - start_clock, 3)
    for key in ("status", "reason", "stages", "utilization", "timing", "commands",
                "tool_versions", "output_tail"):
        if key in body:
            report[key] = body[key]
    stages = report.get("stages") or {}
    bitstream = stages.get("bitstream") or {}
    if isinstance(bitstream, dict) and bitstream.get("status") == "pass":
        report["bitstream"] = {"path": bitstream.get("artifact"), "sha256": bitstream.get("sha256"),
                               "extension": plan["bitstream_extension"]}
    write_json(project / "reports/implement.json", report)
    (project / "reports/implement.log").write_text(report.get("output_tail", "") or report.get("reason", ""),
                                                  encoding="utf-8")
    if progress:
        progress(f"Implementation {report['status']}: {report['reason']}")
    return report


def _run_ice40(project: Path, plan: dict, *, timeout: int, progress) -> dict:
    from . import ice40

    pins = plan.get("pins")
    if not isinstance(pins, str) or not pins:
        return {"status": "blocked", "reason": "The ice40 target requires a pin constraint (.pcf) file.",
                "stages": _empty_stages()}
    pcf = (project / pins).resolve()
    if project.resolve() not in pcf.parents or not pcf.is_file():
        return {"status": "blocked", "reason": f"iCE40 pin constraints are missing: {pins}",
                "stages": _empty_stages()}
    body = ice40.run(project, {**plan, "pcf": pins}, timeout=timeout, progress=progress)
    return body


def _run_anlogic(project: Path, plan: dict, *, timeout: int, td_root: str | None, progress) -> dict:
    from .cli import do_td

    stages = _empty_stages()
    stages["synthesis"]["tool"] = "Anlogic TD"
    stages["place_route"]["tool"] = "Anlogic TD"
    stages["bitstream"]["tool"] = "Anlogic TD"
    if progress:
        progress(f"Running Anlogic TD ({plan['stage']}) through the existing vendor flow.")
    try:
        code = do_td(project, plan["stage"], td_root)
    except FileNotFoundError as exc:
        return {"status": "blocked", "reason": str(exc), "stages": stages}
    td = _read_json(project / "reports/td.json")
    if code == 2 or not td:
        return {"status": "blocked",
                "reason": "Anlogic TD is gated or produced no report; run `icforge td PROJECT` after the "
                          "simulation gate and board pin mapping are satisfied.",
                "stages": stages}
    return _map_td(td, plan, stages)


def _map_td(td: dict, plan: dict, stages: dict) -> dict:
    passed = td.get("status") == "pass"
    stage = td.get("stage")
    full = stage == "full"
    stages["synthesis"]["status"] = "pass" if passed else "fail"
    stages["place_route"]["status"] = "pass" if (passed and full) else ("fail" if not passed and full else "not_run")
    bitstreams = td.get("bitstreams") or []
    bitstream_hashes = td.get("bitstream_hashes") or {}
    if full and passed and bitstreams:
        first = bitstreams[0]
        stages["bitstream"]["status"] = "pass"
        stages["bitstream"]["artifact"] = first
        stages["bitstream"]["sha256"] = bitstream_hashes.get(first)
    elif full and not passed:
        stages["bitstream"]["status"] = "fail"
    report = {
        "status": "pass" if passed else "error",
        "reason": (f"Anlogic TD {stage} run passed." if passed
                   else td.get("output", "Anlogic TD failed.")[-300:].strip() or "Anlogic TD failed."),
        "stages": stages,
        "utilization": td.get("resource_summary") or {},
        "timing": td.get("timing_summary") or None,
        "commands": {"anlogic_td": td.get("command")},
        "output_tail": (td.get("output") or "")[-4000:],
    }
    return report


def _run_generic(project: Path, plan: dict, *, timeout: int, progress) -> dict:
    from .synth import run_synth

    stages = _empty_stages()
    stages["synthesis"]["tool"] = "Yosys"
    stages["place_route"]["tool"] = None
    stages["bitstream"]["tool"] = None
    synth = run_synth(project, top=plan["top"], timeout=timeout, progress=progress)
    status = synth.get("status")
    if status == "blocked":
        return {"status": "blocked", "reason": synth.get("reason", "Yosys is not available."), "stages": stages,
                "tool_versions": {"yosys": synth.get("tool_version")}}
    if status != "pass":
        stages["synthesis"]["status"] = "fail"
        return {"status": "error", "reason": synth.get("reason", "generic synthesis failed."), "stages": stages,
                "tool_versions": {"yosys": synth.get("tool_version")},
                "output_tail": synth.get("output_tail", "")}
    netlist = synth.get("netlist") or {}
    stages["synthesis"].update(status="pass", artifact=netlist.get("path"), sha256=netlist.get("sha256"))
    return {
        "status": "pass",
        "reason": (f"Technology-independent synthesis passed ({synth.get('cells')} cells); "
                   "the generic target offers no place-and-route or bitstream."),
        "stages": stages,
        "tool_versions": {"yosys": synth.get("tool_version")},
        "commands": {"synthesis": synth.get("command")},
        "output_tail": synth.get("output_tail", ""),
    }


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def implement_valid(project: Path, manifest: dict, report: dict) -> tuple[bool, str]:
    if not isinstance(report, dict):
        return False, "Implementation report is not an object"
    if report.get("kind") != "implement":
        return False, "Implementation report kind mismatch"
    if report.get("status") != "pass":
        return False, f"Implementation has not passed ({report.get('status')})"
    try:
        plan = resolve_implement_plan(manifest, target=(report.get("target") or {}).get("id"),
                                      top=report.get("top"), sources=report.get("sources"),
                                      stage=report.get("stage"))
        plan["sources"] = absolute_sources(project, plan["sources"], label="implementation sources")
        expected = _implement_inputs(project, plan)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return False, str(exc)
    if report.get("inputs") != expected:
        return False, "RTL or constraints changed since implementation"
    capabilities = report.get("capabilities") or {}
    stages = report.get("stages") or {}
    for key in STAGE_KEYS:
        if not capabilities.get(key):
            continue
        stage = stages.get(key) or {}
        if stage.get("status") != "pass":
            return False, f"implementation stage {key} did not pass"
    bitstream = report.get("bitstream")
    if isinstance(bitstream, dict) and bitstream.get("path"):
        path = (project / bitstream["path"]).resolve()
        if project.resolve() not in path.parents or not path.is_file():
            return False, "Implementation bitstream is missing"
        if sha(path) != bitstream.get("sha256"):
            return False, "Implementation bitstream was modified"
    return True, "Implementation artifacts are current for the selected target"
