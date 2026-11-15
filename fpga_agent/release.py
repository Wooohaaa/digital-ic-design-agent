"""Versioned release packaging and aggregate engineering signoff (Stage 7).

Stage 7 closes the loop: it runs the full engineering review (all gates, both
structural and functional, including CDC and timing), records a versioned
release manifest, and hands off to the existing delivery packaging.

Honesty contract (see ``docs/release.md``):

* ``release.json`` records the **aggregate** review verdict and each gate's
  status; it does not turn blocked/not_run gates into passes.
* ``artifact_ready`` reflects whether the *required* gates pass on current
  inputs; optional gates (``required: false``) may still be ``not_run``/
  ``blocked`` without blocking the release.
* ``engineering_signoff`` remains ``not_granted`` unless the project
  explicitly grants it; a release is not a chip signoff.
* Input hashes bind the release to the RTL, testbench, constraints and spec.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .evidence import input_hashes
from .project import load_manifest
from .quality import review_project
from .terminal import write_json

RELEASE_VERSION = 1
DEFAULT_VERSION = "0.6.0"


def run_release(project: Path, *, version: str | None = None) -> dict:
    """Run the aggregate review and write ``reports/release.json``."""
    project = project.resolve()
    manifest = load_manifest(project)
    started_at = datetime.now(timezone.utc).isoformat()
    review = review_project(project, "fpga", write=True)
    gates = review.get("gates") or []
    artifact_ready = review.get("artifact_ready")
    required_open = review.get("required_open_items")
    signoff = review.get("engineering_signoff")
    release: dict = {
        "kind": "release",
        "release_version": RELEASE_VERSION,
        "project": manifest.get("project", project.name),
        "version": version or DEFAULT_VERSION,
        "aggregate": {
            "artifact_ready": artifact_ready,
            "engineering_signoff": signoff,
            "required_open_items": required_open,
        },
        "gates": {
            gate.get("id"): {"status": gate.get("status"),
                             "required": gate.get("required")}
            for gate in gates if gate.get("id")
        },
        "inputs": input_hashes(project, manifest),
        "reason": (
            f"Aggregate review: artifact_ready={artifact_ready}, "
            f"signoff={signoff}, required_open_items={required_open}."),
        "started_at": started_at,
    }
    write_json(project / "reports/release.json", release)
    return release
