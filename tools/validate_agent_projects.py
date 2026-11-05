"""Run the host-owned acceptance flow for the multi-IP agent fixtures."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from fpga_agent.cli import do_td
from fpga_agent.quality import lint_project, review_project
from fpga_agent.verify import verify_project


PROJECTS = ("sync_fifo", "spi_master", "apb_pwm_timer")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--synth", action="store_true", help="also run Anlogic TD synthesis-only")
    parser.add_argument("--project", choices=PROJECTS, help="run one fixture")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    selected = (args.project,) if args.project else PROJECTS
    results = []
    for name in selected:
        project = root / "examples" / "agent_validation" / name
        simulation = verify_project(project)
        lint = lint_project(project)
        review = review_project(project, "simulation")
        gates = {gate["id"]: gate["status"] for gate in review["gates"]}
        row = {
            "project": name,
            "simulation": simulation.status,
            "lint": lint["status"],
            "review_artifact_ready": review["artifact_ready"],
            "review_gates": gates,
        }
        passed = simulation.status == "pass" and lint["status"] == "pass" and review["artifact_ready"]
        if args.synth:
            code = do_td(project, "synth", None, update_manifest=False)
            td_report_path = project / "reports" / "td.json"
            try:
                td_report = json.loads(td_report_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                td_report = {}
            row["td_synth"] = td_report.get("status", "missing")
            row["td_stage"] = td_report.get("stage")
            row["td_returncode"] = td_report.get("returncode")
            passed = passed and code == 0 and row["td_synth"] == "pass"
        results.append(row)
        print(json.dumps(row, ensure_ascii=False, sort_keys=True))
    return 0 if all(
        row["simulation"] == "pass"
        and row["lint"] == "pass"
        and row["review_artifact_ready"]
        and (not args.synth or row.get("td_synth") == "pass")
        for row in results
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
