"""Safe, evidence-aware retention for generated Anlogic TD run folders."""
from __future__ import annotations

import json
import shutil
from pathlib import Path


def prune_td_runs(project: Path, *, keep: int = 5, apply: bool = False) -> dict:
    """Plan or remove old TD runs, preserving recent and report-referenced runs.

    The default is a dry run. Only immediate, non-symlink ``run_*`` children of
    ``project/td/runs`` can be removed. Any run name mentioned in a report is
    retained, including the run bound by the current ``reports/td.json``.
    """
    if type(keep) is not int or keep < 1:
        raise ValueError("keep must be a positive integer")
    project = Path(project).resolve()
    runs_root = project / "td" / "runs"
    if not runs_root.exists():
        return {"project": str(project), "keep": keep, "apply": apply,
                "preserved": [], "candidates": [], "removed": []}
    if runs_root.is_symlink() or not runs_root.is_dir() or project not in runs_root.resolve().parents:
        raise ValueError("TD runs path must be a real directory inside the project")

    runs = sorted((path for path in runs_root.iterdir()
                   if path.name.startswith("run_") and path.is_dir() and not path.is_symlink()),
                  key=lambda path: (path.stat().st_mtime_ns, path.name), reverse=True)
    protected = {path.name for path in runs[:keep]}
    reports = project / "reports"
    if reports.is_dir():
        for report in reports.rglob("*"):
            if not report.is_file() or report.suffix.lower() not in {".json", ".md", ".txt", ".log"}:
                continue
            try:
                contents = report.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            protected.update(run.name for run in runs if run.name in contents)

    candidates = [run for run in runs if run.name not in protected]
    removed = []
    if apply:
        for run in candidates:
            resolved = run.resolve()
            if run.is_symlink() or resolved.parent != runs_root.resolve():
                raise ValueError(f"Refusing to remove path outside TD run root: {run}")
            shutil.rmtree(resolved)
            removed.append(run.name)
    return {
        "project": str(project),
        "keep": keep,
        "apply": apply,
        "preserved": [run.name for run in runs if run.name in protected],
        "candidates": [run.name for run in candidates],
        "removed": removed,
    }
