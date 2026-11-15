from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from fpga_agent.timing import _resolve_post_route_simulator, run_timing, timing_valid

FIXTURE = Path(__file__).resolve().parent.parent / "examples" / "agent_validation" / "ice40_pwm"


def _copy_fixture(destination: Path) -> Path:
    shutil.copytree(FIXTURE, destination,
                    ignore=shutil.ignore_patterns("build", "reports", "deliverables"))
    return destination


def _stage_implementation(project: Path) -> None:
    """Copy the cached implementation report and bitstream into the copy."""
    (project / "reports").mkdir(exist_ok=True)
    shutil.copy(FIXTURE / "reports/implement.json", project / "reports/implement.json")
    build = project / "build" / "implement"
    build.mkdir(parents=True, exist_ok=True)
    for name in ("pwm_breath.bin", "pwm_breath.asc", "pwm_breath.json", "ice40.ys"):
        source = FIXTURE / "build" / "implement" / name
        if source.is_file():
            shutil.copy(source, build / name)


class PostRouteToolResolutionTests(unittest.TestCase):
    def test_unknown_family_is_blocked(self):
        sim = _resolve_post_route_simulator({"family": "anlogic"})
        self.assertIsNone(sim["command"])
        self.assertIn("No post-route simulator", sim["reason"])

    def test_ice40_without_tool_is_blocked(self):
        sim = _resolve_post_route_simulator({"family": "ice40"})
        # icesim is not installed on this machine, so the resolution must
        # report a reason and no command.
        if not sim["command"]:
            self.assertIn("icesim is not available", sim["reason"])


class TimingRunTests(unittest.TestCase):
    def test_missing_implement_report_is_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            project = _copy_fixture(Path(directory) / "pwm")
            report = run_timing(project, timeout=30)
            self.assertEqual(report["kind"], "timing")
            self.assertEqual(report["status"], "blocked")
            self.assertEqual(report["timing_status"], "not_run")
            self.assertIn("No implementation report", report["reason"])
            self.assertTrue((project / "reports/timing.json").is_file())

    def test_valid_implement_gives_signoff_pass_but_post_route_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            project = _copy_fixture(Path(directory) / "pwm")
            # Stage the cached implementation report and bitstream.
            _stage_implementation(project)
            report = run_timing(project, timeout=30)
            self.assertEqual(report["kind"], "timing")
            self.assertEqual(report["timing_status"], "pass")
            self.assertEqual(report["post_route_status"], "blocked")
            # Overall is blocked because post-route simulation did not run.
            self.assertEqual(report["status"], "blocked")
            self.assertIn("icesim", report["reason"])
            manifest = json.loads((project / "project.json").read_text(encoding="utf-8"))
            valid, reason = timing_valid(project, manifest, report)
            self.assertFalse(valid)
            self.assertIn("blocked", reason)


class TimingGateTests(unittest.TestCase):
    def test_gate_is_not_run_without_a_report(self):
        with tempfile.TemporaryDirectory() as directory:
            project = _copy_fixture(Path(directory) / "pwm")
            from fpga_agent.quality import review_project
            gates = {gate["id"]: gate for gate in review_project(project, "fpga", write=False)["gates"]}
            self.assertIn("TIM-001", gates)
            self.assertEqual(gates["TIM-001"]["status"], "not_run")
            self.assertFalse(gates["TIM-001"]["required"])

    def test_gate_reports_blocked_with_a_timing_report(self):
        with tempfile.TemporaryDirectory() as directory:
            project = _copy_fixture(Path(directory) / "pwm")
            _stage_implementation(project)
            run_timing(project, timeout=30)
            from fpga_agent.quality import review_project
            gates = {gate["id"]: gate for gate in review_project(project, "fpga", write=False)["gates"]}
            self.assertEqual(gates["TIM-001"]["status"], "blocked")


class TimingCliTests(unittest.TestCase):
    def test_timing_subcommand_parses(self):
        from fpga_agent.cli import parser
        args = parser().parse_args(["timing", "examples/design", "--timeout", "120"])
        self.assertEqual(args.command, "timing")
        self.assertEqual(args.timeout, 120)


if __name__ == "__main__":
    unittest.main()
