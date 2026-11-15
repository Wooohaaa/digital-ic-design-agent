"""RTL structural + functional coverage evidence via Verilator ``--coverage``.

Stage 2 made the implementation layer vendor-neutral; Stage 3 closes the
*verification* loop on the RTL itself with structural coverage; Stage 5 adds
**functional** coverage (``cover property`` -> Verilator ``user`` coverage
points) and **concurrent-assertion** checking on top of the same build/run
pipeline.

Honesty contract (see ``docs/coverage.md``):

* ``pass`` means the build succeeded, the testbench ran cleanly (a ``PASS``
  line, no ``FAIL``/``FATAL``/``ERROR``), no ``assert property`` fired, and
  every declared RTL source has at least one recorded coverage point with a
  nonzero hit count for each point type the tool emitted. Coverage is
  *structural* (line/branch/toggle/expr) and, when requested, *functional*
  (``cover property`` points). It is not assertion *coverage* (how many
  assertions fired) and it is not a claim that all behaviour was exercised.
* A missing ``line``/``branch``/``toggle`` point on an RTL source is reported
  as ``blocked`` with the uncovered points, never silently read as covered.
* A point declared in ``coverage.unreachable`` (with an ``object`` and a
  written ``reason``) is excluded from the denominator and recorded under
  ``waived_unreachable`` with its justification. A waived point is never
  counted as covered; the waiver is an explicit, auditable declaration of a
  bounded proof, not a pass.
* When ``--functional-coverage`` (or ``manifest.coverage.functional_coverage``)
  is requested and the RTL emits no ``cover property`` points, the report is
  ``blocked`` (the request cannot be fulfilled), not a bare ``pass``.
* Editing any RTL or testbench source invalidates the recorded input hashes.
* When Verilator, a C++ compiler or ``make`` is unavailable the gate reports
  ``blocked`` instead of pretending success.
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
from .svsim import _resolve_toolchain, _run
from .terminal import write_json
from .yosys import absolute_sources

COVERAGE_VERSION = 2
DEFAULT_CFLAGS = "-O2"
COV_WNO = ("-Wno-WIDTHEXPAND", "-Wno-WIDTHTRUNC", "-Wno-PROCASSINIT",
           "-Wno-DECLFILENAME")
_ASSERT_FAIL_RE = re.compile(r"(?i)assertion failed")


def resolve_coverage_plan(manifest: dict, *, top: str | None = None,
                           sources: list[str] | None = None,
                           testbench_top: str | None = None,
                           testbench_sources: list[str] | None = None,
                           cflags: str | None = None,
                           functional_coverage: bool | None = None) -> dict:
    """Resolve the coverage intent from arguments or the manifest.

    Coverage needs the RTL under test plus a driving testbench. The testbench
    top defaults to ``manifest.testbench_top``; the RTL top defaults to
    ``manifest.top_module``. ``functional_coverage`` (default from
    ``manifest.coverage.functional_coverage``) requests that ``cover property``
    points (Verilator ``user`` coverage points) be collected and required for a
    full pass.

    ``rtl_sources`` is the full set of RTL compiled into the model. Optional
    ``coverage.sources`` narrows the *measured* set, which is how an
    integration fixture scopes structural coverage to the logic it owns while
    still compiling reused IP (for example, a vendored CPU core that carries
    its own verification suite). When ``coverage.sources`` is absent the
    measured set equals the compiled set, so existing manifests are unchanged.

    Optional ``coverage.unreachable`` is a list of ``{"object": name,
    "reason": text}`` declarations for points that are provably unreachable
    (not merely unexercised). Each matched point is excluded from the
    denominator and recorded under ``waived_unreachable`` with its reason; a
    waived point is never counted as covered. Declarations that match no point
    are surfaced as ``waiver_unmatched`` so they cannot silently rot.
    """
    section = manifest.get("coverage") if isinstance(manifest.get("coverage"), dict) else {}
    resolved_rtl_top = top or section.get("top") or manifest.get("top_module")
    if not isinstance(resolved_rtl_top, str) or not resolved_rtl_top.strip():
        raise ValueError("coverage requires an RTL top module (manifest top_module)")
    resolved_sources = sources if sources is not None else manifest.get("rtl_sources")
    if not isinstance(resolved_sources, list) or not resolved_sources:
        raise ValueError("coverage requires a nonempty RTL source list")
    measure_sources = section.get("sources")
    if measure_sources is None:
        measure_sources = resolved_sources
    if not isinstance(measure_sources, list) or not measure_sources:
        raise ValueError("coverage.sources must be a nonempty list when present")
    raw_unreachable = section.get("unreachable") or []
    if not isinstance(raw_unreachable, list) or any(
            not isinstance(item, dict) or "object" not in item for item in raw_unreachable):
        raise ValueError("coverage.unreachable must be a list of objects with an 'object' key")
    resolved_tb_top = testbench_top or section.get("testbench_top") or manifest.get("testbench_top")
    resolved_tb = testbench_sources if testbench_sources is not None else manifest.get("testbench_sources")
    if not isinstance(resolved_tb_top, str) or not resolved_tb_top.strip():
        raise ValueError("coverage requires a testbench top (manifest testbench_top)")
    if not isinstance(resolved_tb, list) or not resolved_tb:
        raise ValueError("coverage requires a nonempty testbench source list")
    resolved_cflags = cflags if cflags is not None else section.get("cflags") or DEFAULT_CFLAGS
    if not isinstance(resolved_cflags, str):
        raise ValueError("coverage cflags must be a string")
    if functional_coverage is None:
        resolved_functional = bool(section.get("functional_coverage", False))
    elif functional_coverage is True or functional_coverage is False:
        resolved_functional = functional_coverage
    else:
        raise ValueError("functional_coverage must be a bool")
    return {
        "top": resolved_rtl_top,
        "sources": list(resolved_sources),
        "measure_sources": list(measure_sources),
        "unreachable": [dict(item) for item in raw_unreachable],
        "testbench_top": resolved_tb_top,
        "testbench_sources": list(resolved_tb),
        "cflags": resolved_cflags,
        "functional_coverage": resolved_functional,
    }


def _coverage_inputs(project: Path, plan: dict) -> dict:
    result = hdl_input_hashes(project, plan["sources"] + plan["testbench_sources"])
    for name in ("spec/brief.md", "spec/design.md", "spec/verification-plan.json"):
        path = project / name
        if path.is_file():
            result[name] = sha(path)
    result["@checker"] = sha(Path(__file__))
    config = {"top": plan["top"], "sources": plan["sources"],
              "measure_sources": plan.get("measure_sources", plan["sources"]),
              "testbench_top": plan["testbench_top"],
              "testbench_sources": plan["testbench_sources"],
              "cflags": plan["cflags"],
              "functional_coverage": plan["functional_coverage"],
              "unreachable": plan.get("unreachable", []),
              "version": COVERAGE_VERSION}
    result["@coverage_config"] = hashlib.sha256(
        json.dumps(config, sort_keys=True).encode("utf-8")).hexdigest()
    return result


# --- coverage.dat parsing (SystemC coverage record format) -------------------

_RECORD = re.compile(r"^C\s+'(.*)$")


def _parse_coverage_dat(path: Path, project: Path) -> list[dict]:
    """Parse a Verilator coverage.dat into structured point records.

    Records are of the form::

        C '<field>\\x01<field>\\x01...

    where each ``field`` is ``<key>\\x02<value>``. The keys are ``f`` (file),
    ``l`` (line), ``t`` (type: line/branch/toggle/expr/user), ``o`` (object),
    ``h`` (hit count as ``"<scope>' <n>"``). Functional ``cover property``
    points have type ``user`` and an object like ``cover|S<line>``.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    project_root = project.resolve()
    points: list[dict] = []
    for raw in text.splitlines():
        match = _RECORD.match(raw.strip())
        if not match:
            continue
        body = match.group(1)
        fields: dict[str, str] = {}
        for part in body.split("\x01"):
            if "\x02" in part:
                key, value = part.split("\x02", 1)
                fields[key] = value
        if "f" not in fields or "t" not in fields or "h" not in fields:
            continue
        source = fields["f"].replace("\\", "/")
        rel = Path(source).name
        if not (rel.endswith(".v") or rel.endswith(".sv")):
            continue
        # Hit count is stored as "<scope>' <n>"; the scope may itself contain a
        # quote, so take the trailing integer after the last space.
        hit_text = fields["h"].strip()
        tail = hit_text.rsplit(None, 1)[-1] if " " in hit_text else hit_text
        try:
            hits = int(tail.strip().strip("'"))
        except ValueError:
            hits = 0
        normalized = source
        for root in (str(project_root), project_root.as_posix()):
            if source.startswith(root):
                normalized = source[len(root):].lstrip("/\\")
                break
        points.append({
            "file": normalized or rel,
            "basename": rel,
            "line": int(fields.get("l", 0)) if fields.get("l", "").isdigit() else None,
            "type": fields["t"],
            "object": fields.get("o", ""),
            "hits": hits,
        })
    return points


def _match_source(points: list[dict], by_file: dict, basename: str) -> str | None:
    for source in by_file:
        if by_file[source]["basename"] == basename:
            return source
    return None


def _summarize(points: list[dict], rtl_sources: list[str],
               unreachable: list[dict] | None = None) -> dict:
    """Aggregate *structural* coverage points per RTL source and per type.

    Structural types are ``line``/``branch``/``toggle``/``expr``. Functional
    ``user`` points are excluded here; use :func:`_summarize_functional`.

    ``unreachable`` is a list of ``{"object": ..., "line": optional}`` entries
    from ``coverage.unreachable``. Points whose object matches a declaration are
    excluded from the denominator (never counted as covered) and collected in
    ``waived_points`` so the report records exactly which points were waived and
    with what justification. Matching is by object name (exact); an optional
    ``line`` field narrows the match.
    """
    by_file: dict[str, dict[str, dict]] = {}
    for source in rtl_sources:
        basename = Path(source).name
        by_file[source] = {"basename": basename, "points": 0,
                           "covered": 0, "types": {}, "waived_points": []}
    for point in points:
        if point["type"] == "user":
            continue
        target = _match_source(points, by_file, point["basename"])
        if target is None:
            continue
        entry = by_file[target]
        if _is_unreachable(point, unreachable or []):
            entry["waived_points"].append({
                "type": point["type"], "line": point.get("line"),
                "object": point.get("object", ""),
            })
            continue
        entry["points"] += 1
        if point["hits"] > 0:
            entry["covered"] += 1
        type_entry = entry["types"].setdefault(
            point["type"], {"points": 0, "covered": 0})
        type_entry["points"] += 1
        if point["hits"] > 0:
            type_entry["covered"] += 1
    total_points = sum(e["points"] for e in by_file.values())
    total_covered = sum(e["covered"] for e in by_file.values())
    total_waived = sum(len(e.get("waived_points", [])) for e in by_file.values())
    return {
        "by_source": by_file,
        "total_points": total_points,
        "total_covered": total_covered,
        "total_waived": total_waived,
        "coverage_percent": round(100.0 * total_covered / total_points, 2) if total_points else 0.0,
    }


def _match_unreachable(point: dict, unreachable: list[dict]) -> dict | None:
    """Return the ``coverage.unreachable`` declaration matching *point*, if any.

    Each declaration has an ``object`` name; a declaration may also carry a
    ``line`` to disambiguate. Returns the declaration itself so the report can
    record its justification; None when no declaration matches.
    """
    obj = point.get("object", "")
    line = point.get("line")
    for item in unreachable:
        declared_obj = item.get("object")
        if declared_obj is None or declared_obj != obj:
            continue
        if "line" in item and item["line"] != line:
            continue
        return item
    return None


def _is_unreachable(point: dict, unreachable: list[dict]) -> bool:
    """Return True if *point* matches any ``coverage.unreachable`` declaration."""
    return _match_unreachable(point, unreachable) is not None


def _summarize_functional(points: list[dict], rtl_sources: list[str]) -> dict:
    """Aggregate *functional* coverage points (Verilator ``user``/cover) per source.

    Each ``cover property`` becomes one point whose hit count is the number of
    times the property sampled true. ``covered`` counts points with a nonzero
    hit. A point whose hits are zero is listed in ``uncovered_points``.
    """
    by_file: dict[str, dict[str, dict]] = {}
    for source in rtl_sources:
        basename = Path(source).name
        by_file[source] = {"basename": basename, "points": 0, "covered": 0,
                           "points_detail": []}
    for point in points:
        if point["type"] != "user":
            continue
        target = _match_source(points, by_file, point["basename"])
        if target is None:
            continue
        entry = by_file[target]
        entry["points"] += 1
        hit = point["hits"] > 0
        if hit:
            entry["covered"] += 1
        entry["points_detail"].append({
            "line": point["line"],
            "object": point["object"],
            "hits": point["hits"],
            "covered": hit,
        })
    total_points = sum(e["points"] for e in by_file.values())
    total_covered = sum(e["covered"] for e in by_file.values())
    uncovered = []
    for source, entry in by_file.items():
        for detail in entry["points_detail"]:
            if not detail["covered"]:
                uncovered.append({
                    "source": source, "line": detail["line"],
                    "object": detail["object"],
                })
    return {
        "by_source": by_file,
        "total_points": total_points,
        "total_covered": total_covered,
        "coverage_percent": round(100.0 * total_covered / total_points, 2) if total_points else 0.0,
        "uncovered_points": uncovered,
    }


def _evaluate_runtime(project: Path, returncode: int | None, runtime_output: str) -> tuple[bool, str]:
    failed = bool(re.search(r"(?im)^\s*(?:FAIL|FATAL|ERROR)(?:\b|:)", runtime_output))
    passed_marker = bool(re.search(r"(?m)^\s*PASS(?:\b|:)", runtime_output))
    if returncode == 124:
        return False, "Coverage simulation timed out."
    if returncode not in (0, None):
        return False, f"Simulation exited with return code {returncode}."
    if failed or not passed_marker:
        return False, "Testbench did not report a clean pass line."
    return True, "Self-checking testbench passed."


def _assertion_failure(output: str) -> str | None:
    """Return the first line mentioning a failed ``assert property``, if any.

    Verilator reports a fired concurrent assertion as ``%Error: ... Assertion
    failed`` and aborts with a non-zero exit code. We surface that explicitly
    rather than folding it into a generic runtime failure.
    """
    for line in output.splitlines():
        if _ASSERT_FAIL_RE.search(line):
            return line.strip()[:300]
    return None


def run_coverage(project: Path, *, top: str | None = None, sources: list[str] | None = None,
                 testbench_top: str | None = None, testbench_sources: list[str] | None = None,
                 cflags: str | None = None, functional_coverage: bool | None = None,
                 timeout: int = 300,
                 progress: Callable[[str], None] | None = None) -> dict:
    """Build and run a self-checking testbench with Verilator coverage.

    When ``functional_coverage`` is true the ``cover property`` points in
    ``coverage.dat`` are collected and required for a ``pass``. Fired
    ``assert property`` statements always fail the run regardless of this flag.
    """
    if type(timeout) is not int or not 1 <= timeout <= 86400:
        raise ValueError("timeout must be 1..86400 seconds")
    project = project.resolve()
    manifest = load_manifest(project)
    plan = resolve_coverage_plan(manifest, top=top, sources=sources,
                                 testbench_top=testbench_top,
                                 testbench_sources=testbench_sources, cflags=cflags,
                                 functional_coverage=functional_coverage)
    plan["sources"] = absolute_sources(project, plan["sources"], label="coverage sources")
    plan["testbench_sources"] = absolute_sources(
        project, plan["testbench_sources"], label="coverage testbench sources")
    inputs = _coverage_inputs(project, plan)
    started_at = datetime.now(timezone.utc).isoformat()
    tool = _resolve_toolchain()
    obj_rel = Path("build") / "coverage" / "obj"
    executable = obj_rel / ("sim.exe" if os.name == "nt" else "sim")
    report: dict = {
        "schema_version": 1, "coverage_version": COVERAGE_VERSION, "kind": "coverage",
        "project": manifest.get("project", project.name), "status": "blocked",
        "structural_status": "blocked", "functional_status": "not_run",
        "functional_requested": plan["functional_coverage"],
        "top": plan["top"], "sources": plan["sources"],
        "testbench_top": plan["testbench_top"], "testbench_sources": plan["testbench_sources"],
        "cflags": plan["cflags"],
        "tool": "Verilator", "tool_version": tool["version"], "cxx": tool["cxx"],
        "make": tool["make"], "command": [], "obj_dir": obj_rel.as_posix(),
        "executable": executable.as_posix(), "inputs": inputs,
        "returncode": None, "started_at": started_at, "elapsed_seconds": 0.0,
        "coverage": None, "functional": None, "assertion_failure": None,
        "reason": tool["reason"] or "Verilator is not available.",
        "output_tail": "",
    }
    if not tool["command"] or tool["reason"]:
        if progress:
            progress(f"Coverage blocked: {report['reason']}")
        write_json(project / "reports/coverage.json", report)
        (project / "reports/coverage.log").write_text(report["reason"] + "\n", encoding="utf-8")
        return report

    obj_dir = project / obj_rel
    obj_dir.mkdir(parents=True, exist_ok=True)
    # The coverage dump lands in the cwd as coverage.dat; run from the project
    # root so it is captured deterministically.
    command = tool["command"] + [
        "--binary", "--timing", "--coverage", "--top-module", plan["testbench_top"],
        "-Mdir", str(obj_dir), "-o", "sim", "-CFLAGS", plan["cflags"],
        "-I" + str(project), *COV_WNO,
        *[str(project / name) for name in plan["sources"]],
        *[str(project / name) for name in plan["testbench_sources"]],
    ]
    report["command"] = command
    if progress:
        progress(f"Building coverage model {plan['testbench_top']} via {tool['version'] or 'Verilator'}.")
    start_clock = time.monotonic()
    build_returncode, build_output = _run(command, project, tool["env"], timeout)
    report["output_tail"] = build_output[-4000:]
    exe_path = project / executable
    coverage_dat = project / "coverage.dat"
    if build_returncode == 124:
        report["status"] = "error"
        report["structural_status"] = "error"
        report["elapsed_seconds"] = round(time.monotonic() - start_clock, 3)
        report["reason"] = f"Verilator coverage build timed out after {timeout}s."
        write_json(project / "reports/coverage.json", report)
        (project / "reports/coverage.log").write_text(build_output, encoding="utf-8")
        if progress:
            progress(f"Coverage {report['status']}: {report['reason']}")
        return report
    if build_returncode not in (0, None) or not exe_path.is_file():
        report["status"] = "error"
        report["structural_status"] = "error"
        report["elapsed_seconds"] = round(time.monotonic() - start_clock, 3)
        report["reason"] = _first_error(build_output) or "Verilator failed to build the coverage model."
        write_json(project / "reports/coverage.json", report)
        (project / "reports/coverage.log").write_text(build_output, encoding="utf-8")
        if progress:
            progress(f"Coverage {report['status']}: {report['reason']}")
        return report

    if progress:
        progress(f"Running coverage simulation {executable.name}.")
    run_clock = time.monotonic()
    run_returncode, run_output = _run([str(exe_path)], project, tool["env"], timeout)
    report["elapsed_seconds"] = round((time.monotonic() - start_clock), 3)
    report["returncode"] = run_returncode
    report["output_tail"] = (build_output + run_output)[-4000:]
    assertion_failure = _assertion_failure(build_output + run_output)
    report["assertion_failure"] = assertion_failure
    ok, reason = _evaluate_runtime(project, run_returncode, run_output)
    if assertion_failure:
        ok = False
        reason = f"Concurrent assertion failed: {assertion_failure}"
    if not ok:
        report["status"] = "fail"
        report["structural_status"] = "fail"
        report["functional_status"] = "fail"
        report["reason"] = reason
        write_json(project / "reports/coverage.json", report)
        (project / "reports/coverage.log").write_text(build_output + run_output, encoding="utf-8")
        if progress:
            progress(f"Coverage {report['status']}: {report['reason']}")
        return report

    # coverage.dat is written at exit into the cwd (project root).
    points = _parse_coverage_dat(coverage_dat, project)
    summary = _summarize(points, plan["measure_sources"], plan["unreachable"])
    functional = _summarize_functional(points, plan["measure_sources"])
    report["coverage"] = summary
    report["functional"] = functional if functional["total_points"] else None

    # Record waived points with their justifications, and surface any
    # ``coverage.unreachable`` declaration that matched no point (a typo or a
    # stale waiver is reported, never silently dropped).
    waived = []
    matched: set = set()
    for source, entry in summary["by_source"].items():
        for item in entry.get("waived_points", []):
            declaration = _match_unreachable(item, plan["unreachable"])
            matched.add((item.get("object"), declaration.get("line")
                         if declaration else item.get("line")))
            waived.append({
                "source": source, "type": item.get("type"),
                "line": item.get("line"), "object": item.get("object", ""),
                "reason": declaration.get("reason", "") if declaration else "",
            })
    report["waived_unreachable"] = waived
    report["waiver_unmatched"] = [
        {"object": item.get("object"), "line": item.get("line")}
        for item in plan["unreachable"]
        if (item.get("object"), item.get("line")) not in matched
    ]

    uncovered = []
    for source, entry in summary["by_source"].items():
        if entry["points"] == 0:
            if not entry.get("waived_points"):
                uncovered.append(f"{source}: no coverage points emitted")
            continue
        for point_type, type_entry in entry["types"].items():
            if type_entry["covered"] < type_entry["points"]:
                uncovered.append(
                    f"{source}: {type_entry['points'] - type_entry['covered']} "
                    f"{point_type} points uncovered")
    if uncovered:
        report["structural_status"] = "blocked"
    else:
        report["structural_status"] = "pass"

    if plan["functional_coverage"]:
        if functional["total_points"] == 0:
            report["functional_status"] = "blocked"
        elif functional["total_covered"] < functional["total_points"]:
            report["functional_status"] = "blocked"
        else:
            report["functional_status"] = "pass"
    else:
        report["functional_status"] = "not_run"

    # Overall status: structural first, then functional when requested.
    if report["structural_status"] != "pass":
        report["status"] = report["structural_status"]
    elif plan["functional_coverage"] and report["functional_status"] != "pass":
        report["status"] = "blocked"
    else:
        report["status"] = "pass"

    parts = []
    if report["structural_status"] == "pass":
        parts.append(
            f"Structural coverage {summary['total_covered']}/{summary['total_points']} "
            f"RTL points ({summary['coverage_percent']}%).")
    else:
        parts.append("Structural coverage incomplete: " + "; ".join(uncovered[:6]) +
                     ("." if len(uncovered) <= 6 else f" (+{len(uncovered) - 6} more)."))
    if summary.get("total_waived"):
        parts.append(
            f"{summary['total_waived']} structurally-unreachable point(s) waived "
            f"(see coverage.unreachable; recorded under waived_unreachable, not covered).")
    if report["waiver_unmatched"]:
        parts.append(
            f"{len(report['waiver_unmatched'])} coverage.unreachable declaration(s) "
            f"matched no point (stale or mistyped waiver).")
    if plan["functional_coverage"]:
        if report["functional_status"] == "pass":
            parts.append(
                f"Functional coverage {functional['total_covered']}/"
                f"{functional['total_points']} cover points.")
        elif functional["total_points"] == 0:
            parts.append("Functional coverage requested but no cover property points were emitted.")
        else:
            fu = [f"{item['source']}:L{item['line']} {item['object']}"
                  for item in functional["uncovered_points"][:6]]
            parts.append("Functional coverage incomplete: " + "; ".join(fu) +
                         ("." if len(functional["uncovered_points"]) <= 6
                          else f" (+{len(functional['uncovered_points']) - 6} more)."))
    report["reason"] = " ".join(parts)
    write_json(project / "reports/coverage.json", report)
    (project / "reports/coverage.log").write_text(build_output + run_output, encoding="utf-8")
    if progress:
        progress(f"Coverage {report['status']}: {report['reason']}")
    return report


def _first_error(output: str) -> str:
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("%Error"):
            return stripped[:300]
    return ""


def coverage_valid(project: Path, manifest: dict, report: dict) -> tuple[bool, str]:
    """Validate the *structural* coverage evidence (gate ``COV-001``)."""
    project = project.resolve()
    if not isinstance(report, dict):
        return False, "Coverage report is not an object"
    if report.get("kind") != "coverage":
        return False, "Coverage report kind mismatch"
    try:
        plan = resolve_coverage_plan(manifest, top=report.get("top"),
                                     sources=report.get("sources"),
                                     testbench_top=report.get("testbench_top"),
                                     testbench_sources=report.get("testbench_sources"),
                                     cflags=report.get("cflags"),
                                     functional_coverage=report.get("functional_requested"))
        plan["sources"] = absolute_sources(project, plan["sources"], label="coverage sources")
        plan["testbench_sources"] = absolute_sources(
            project, plan["testbench_sources"], label="coverage testbench sources")
        expected = _coverage_inputs(project, plan)
    except (OSError, ValueError, KeyError) as exc:
        return False, str(exc)
    if report.get("inputs") != expected:
        return False, "RTL or testbench changed since the coverage run"
    status = report.get("structural_status") or report.get("status")
    if status != "pass":
        return False, f"Coverage has not passed ({status})"
    coverage = report.get("coverage") or {}
    by_source = coverage.get("by_source") or {}
    for source in plan["measure_sources"]:
        entry = by_source.get(source)
        if not entry or entry["points"] == 0:
            return False, f"RTL source {source} has no recorded coverage points"
        if entry["covered"] < entry["points"]:
            return False, f"RTL source {source} has uncovered coverage points"
    return True, "Current structural coverage is complete for the declared RTL"


def functional_coverage_valid(project: Path, manifest: dict, report: dict) -> tuple[bool, str]:
    """Validate the *functional* coverage evidence (gate ``COV-002``)."""
    project = project.resolve()
    if not isinstance(report, dict):
        return False, "Coverage report is not an object"
    if report.get("kind") != "coverage":
        return False, "Coverage report kind mismatch"
    functional = report.get("functional")
    if not isinstance(functional, dict) or functional.get("total_points", 0) == 0:
        return False, "No functional (cover property) coverage points were recorded"
    try:
        plan = resolve_coverage_plan(manifest, top=report.get("top"),
                                     sources=report.get("sources"),
                                     testbench_top=report.get("testbench_top"),
                                     testbench_sources=report.get("testbench_sources"),
                                     cflags=report.get("cflags"),
                                     functional_coverage=True)
        plan["sources"] = absolute_sources(project, plan["sources"], label="coverage sources")
        plan["testbench_sources"] = absolute_sources(
            project, plan["testbench_sources"], label="coverage testbench sources")
        expected = _coverage_inputs(project, plan)
    except (OSError, ValueError, KeyError) as exc:
        return False, str(exc)
    if report.get("inputs") != expected:
        return False, "RTL or testbench changed since the coverage run"
    by_source = functional.get("by_source") or {}
    for source in plan["measure_sources"]:
        entry = by_source.get(source)
        if not entry or entry["points"] == 0:
            return False, f"RTL source {source} has no recorded functional coverage points"
        if entry["covered"] < entry["points"]:
            return False, f"RTL source {source} has uncovered functional coverage points"
    return True, f"Functional coverage complete ({functional['total_covered']}/{functional['total_points']})"
