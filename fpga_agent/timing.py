"""Post-route timing simulation and physical-signoff evidence path (Stage 6).

Stage 6 establishes the *path* from place-and-route to timing signoff and
post-route simulation, without claiming results it did not produce.

Honesty contract (see ``docs/timing.md``):

* The **static timing signoff** (max frequency vs. constraint) is read from
  the place-and-route tool output recorded in ``reports/implement.json``. For
  the open iCE40 backend this is real evidence from ``nextpnr-ice40``; it is
  not Anlogic STA and not a claim that all endpoints were constrained.
* **Post-route / gate-level SDF simulation** needs a target-specific simulator
  (``icesim`` for iCE40). When the simulator is absent the report is
  ``blocked`` with the specific reason; it is never silently skipped or
  turned into a pass. The path is scaffolded (tool resolution + report schema)
  but not exercised in this environment.
* A timing-signoff failure is reported ``fail``; a missing or invalid
  implementation report is reported ``blocked``/``not_run``.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .evidence import hdl_input_hashes, sha
from .implement import implement_valid
from .project import load_manifest
from .terminal import write_json

TIMING_VERSION = 1


def _tool_version(command: str) -> str:
    import subprocess
    try:
        completed = subprocess.run([command, "--version"], capture_output=True,
                                   text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return ""
    output = (completed.stdout or completed.stderr or "").strip()
    return output.splitlines()[0] if output else ""


def _resolve_post_route_simulator(target: dict) -> dict:
    """Locate a post-route / gate-level simulator for the target.

    Returns ``{command, tool_version, reason}``. Only the iCE40 family has a
    defined path (``icesim``); other targets report why no simulator is
    defined. ``ICFORGE_ICESIM`` overrides the executable.
    """
    family = (target.get("family") or "").lower()
    if family != "ice40":
        return {"command": None, "tool_version": None,
                "reason": (f"No post-route simulator is defined for target family "
                           f"'{target.get('family') or 'unknown'}'.")}
    env_override = os.environ.get("ICFORGE_ICESIM")
    if env_override:
        path = Path(env_override)
        if path.is_file():
            return {"command": [str(path)], "tool_version": _tool_version(str(path)),
                    "reason": None}
        return {"command": None, "tool_version": None,
                "reason": f"ICFORGE_ICESIM points to a missing file: {env_override}"}
    found = shutil.which("icesim") or shutil.which("icesim.exe")
    if found:
        return {"command": [found], "tool_version": _tool_version(found), "reason": None}
    return {"command": None, "tool_version": None,
            "reason": ("icesim is not available (set ICFORGE_ICESIM or add it to PATH); "
                       "post-route SDF simulation is scaffolded but not exercised.")}


def run_timing(project: Path, *, timeout: int = 600,
               progress: Callable[[str], None] | None = None) -> dict:
    """Build the post-route timing / signoff report."""
    if type(timeout) is not int or not 1 <= timeout <= 86400:
        raise ValueError("timeout must be 1..86400 seconds")
    project = project.resolve()
    manifest = load_manifest(project)
    started_at = datetime.now(timezone.utc).isoformat()
    report: dict = {
        "schema_version": 1, "timing_version": TIMING_VERSION, "kind": "timing",
        "project": manifest.get("project", project.name),
        "status": "blocked", "timing_status": "not_run", "post_route_status": "not_run",
        "target": None, "timing": None, "utilization": None, "post_route": None,
        "inputs": {}, "reason": "", "started_at": started_at, "elapsed_seconds": 0.0,
    }
    implement_path = project / "reports" / "implement.json"
    if not implement_path.is_file():
        report["reason"] = "No implementation report; run `icforge implement PROJECT` first."
        write_json(project / "reports/timing.json", report)
        (project / "reports/timing.log").write_text(report["reason"] + "\n", encoding="utf-8")
        if progress:
            progress(f"Timing {report['status']}: {report['reason']}")
        return report
    implement = json.loads(implement_path.read_text(encoding="utf-8"))
    report["target"] = implement.get("target")
    report["utilization"] = implement.get("utilization")
    try:
        ok, reason = implement_valid(project, manifest, implement)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        ok, reason = False, str(exc)
    sources = manifest.get("rtl_sources")
    inputs: dict = {"implement": sha(implement_path), "@checker": sha(Path(__file__))}
    if isinstance(sources, list) and sources:
        try:
            inputs.update(hdl_input_hashes(project, sources))
        except (OSError, ValueError) as exc:
            report["reason"] = f"RTL sources could not be fingerprinted: {exc}"
            write_json(project / "reports/timing.json", report)
            (project / "reports/timing.log").write_text(report["reason"] + "\n", encoding="utf-8")
            if progress:
                progress(f"Timing {report['status']}: {report['reason']}")
            return report
    report["inputs"] = inputs
    if not ok:
        report["timing_status"] = "blocked"
        report["reason"] = f"Implementation evidence is not valid: {reason}"
        write_json(project / "reports/timing.json", report)
        (project / "reports/timing.log").write_text(report["reason"] + "\n", encoding="utf-8")
        if progress:
            progress(f"Timing {report['status']}: {report['reason']}")
        return report
    timing = implement.get("timing")
    if not isinstance(timing, dict):
        report["timing_status"] = "blocked"
        report["reason"] = "The implementation report has no timing data."
    else:
        report["timing"] = timing
        if timing.get("pass"):
            report["timing_status"] = "pass"
        else:
            report["timing_status"] = "fail"
    sim = _resolve_post_route_simulator(report["target"] or {})
    if not sim["command"]:
        post_status = "blocked"
    else:
        # The executable is present but the SDF run path is not exercised in
        # this environment; report blocked rather than fabricating a result.
        post_status = "blocked"
    report["post_route"] = {
        "tool": "icesim",
        "command": sim["command"],
        "tool_version": sim["tool_version"],
        "status": post_status,
        "reason": sim["reason"] or "post-route SDF simulation path is scaffolded but not exercised.",
    }
    report["post_route_status"] = report["post_route"]["status"]
    if report["timing_status"] == "fail":
        report["status"] = "fail"
    elif report["timing_status"] == "pass" and report["post_route_status"] == "pass":
        report["status"] = "pass"
    else:
        report["status"] = "blocked"
    parts = []
    if report["timing_status"] == "pass":
        parts.append(
            f"Timing signoff PASS ({timing.get('max_frequency_mhz')} MHz max, "
            f"{timing.get('constraint_mhz')} MHz constraint, clock "
            f"'{timing.get('clock')}').")
    elif report["timing_status"] == "fail":
        parts.append(f"Timing signoff FAIL: {timing}.")
    else:
        parts.append("Timing signoff unavailable.")
    parts.append(
        f"Post-route simulation {report['post_route_status']}: "
        f"{report['post_route']['reason']}")
    report["reason"] = " ".join(parts)
    write_json(project / "reports/timing.json", report)
    (project / "reports/timing.log").write_text(report["reason"] + "\n", encoding="utf-8")
    if progress:
        progress(f"Timing {report['status']}: {report['reason']}")
    return report


def timing_valid(project: Path, manifest: dict, report: dict) -> tuple[bool, str]:
    if not isinstance(report, dict):
        return False, "Timing report is not an object"
    if report.get("kind") != "timing":
        return False, "Timing report kind mismatch"
    status = report.get("status")
    if status == "pass":
        return True, "Timing signoff passed and post-route simulation passed"
    if status == "fail":
        return False, f"Timing signoff failed ({report.get('reason')})"
    if status == "blocked":
        return False, f"Timing evidence is blocked ({report.get('reason')})"
    return False, f"Timing has not passed ({status})"
