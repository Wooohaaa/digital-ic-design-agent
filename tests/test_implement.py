from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fpga_agent.cli import parser
from fpga_agent.implement import (
    implement_valid,
    resolve_implement_plan,
    run_implement,
)

FIXTURE = Path(__file__).resolve().parent.parent / "examples" / "agent_validation" / "ice40_pwm"

ICE_MANIFEST = {
    "project": "pwm", "top_module": "pwm_breath", "rtl_sources": ["rtl/pwm_breath.v"],
    "target": "lattice-ice40-hx1k",
}
GENERIC_MANIFEST = {
    "project": "counter", "top_module": "counter", "rtl_sources": ["rtl/counter.v"],
}


def _copy_fixture(destination: Path) -> Path:
    shutil.copytree(FIXTURE, destination, ignore=shutil.ignore_patterns("build", "reports"))
    return destination


class ImplementPlanTests(unittest.TestCase):
    def test_resolves_ice40_profile(self):
        plan = resolve_implement_plan(ICE_MANIFEST)
        self.assertEqual(plan["backend"], "ice40")
        self.assertEqual(plan["target"]["id"], "lattice-ice40-hx1k")
        self.assertEqual(plan["top"], "pwm_breath")
        self.assertEqual(plan["pins"], "constraints/pins.pcf")

    def test_defaults_target_to_anlogic_and_stage_to_full(self):
        plan = resolve_implement_plan({"project": "x", "top_module": "x", "rtl_sources": ["rtl/x.v"]})
        self.assertEqual(plan["backend"], "anlogic")
        self.assertEqual(plan["stage"], "full")

    def test_generic_defaults_stage_to_synth(self):
        plan = resolve_implement_plan({"project": "x", "top_module": "x", "rtl_sources": ["rtl/x.v"],
                                       "target": "generic"})
        self.assertEqual(plan["stage"], "synth")
        self.assertEqual(plan["backend"], "generic")

    def test_requires_top_module(self):
        with self.assertRaisesRegex(ValueError, "top module"):
            resolve_implement_plan({"rtl_sources": ["rtl/x.v"]})

    def test_requires_rtl_sources(self):
        with self.assertRaisesRegex(ValueError, "source list"):
            resolve_implement_plan({"top_module": "x"})

    def test_anlogic_rejects_unknown_stage(self):
        with self.assertRaisesRegex(ValueError, "synth or full"):
            resolve_implement_plan({"project": "x", "top_module": "x", "rtl_sources": ["rtl/x.v"],
                                    "target": "anlogic-hx1p35a"}, stage="phys")


class ImplementBlockedTests(unittest.TestCase):
    def test_ice40_missing_toolchain_is_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            project = _copy_fixture(Path(directory) / "pwm")
            with patch("fpga_agent.ice40.toolchain",
                       return_value={"yosys": [], "nextpnr": None, "icepack": None,
                                     "missing": ["Yosys", "nextpnr-ice40", "icepack"],
                                     "reason": "The open iCE40 backend requires Yosys."}):
                report = run_implement(project)
            self.assertEqual(report["status"], "blocked")
            self.assertEqual(report["kind"], "implement")
            self.assertTrue((project / "reports/implement.json").is_file())
            valid, reason = implement_valid(project, json.loads(
                (project / "project.json").read_text(encoding="utf-8")), report)
            self.assertFalse(valid)
            self.assertIn("blocked", reason)

    def test_missing_pin_constraint_is_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            project = _copy_fixture(Path(directory) / "pwm")
            (project / "constraints/pins.pcf").unlink()
            report = run_implement(project)
            self.assertEqual(report["status"], "blocked")
            self.assertIn("pin", report["reason"])


class ImplementGenericTests(unittest.TestCase):
    def _build(self, root: Path, rtl: str) -> Path:
        project = root / "counter"
        (project / "rtl").mkdir(parents=True)
        (project / "rtl/counter.v").write_text(rtl, encoding="utf-8", newline="\n")
        (project / "project.json").write_text(json.dumps(
            {"project": "counter", "top_module": "counter", "rtl_sources": ["rtl/counter.v"],
             "target": "generic"}, indent=2) + "\n", encoding="utf-8")
        return project

    COUNTER = """module counter (input wire clk, input wire rst_n, output wire [2:0] q);
  reg [2:0] cnt;
  always @(posedge clk) if (!rst_n) cnt <= 3'd0; else cnt <= cnt + 1'b1;
  assign q = cnt;
endmodule
"""

    def test_generic_backend_synthesizes_without_claiming_bitstream(self):
        with tempfile.TemporaryDirectory() as directory:
            from fpga_agent.synth import yosys_command
            if not yosys_command():
                self.skipTest("Yosys required")
            project = self._build(Path(directory), self.COUNTER)
            report = run_implement(project)
            self.assertEqual(report["status"], "pass", report["reason"])
            self.assertEqual(report["backend"], "generic")
            self.assertEqual(report["stages"]["synthesis"]["status"], "pass")
            self.assertEqual(report["stages"]["place_route"]["status"], "not_run")
            self.assertEqual(report["stages"]["bitstream"]["status"], "not_run")
            self.assertIsNone(report["bitstream"])
            self.assertIn("no place-and-route", report["reason"])
            valid, reason = implement_valid(project, json.loads(
                (project / "project.json").read_text(encoding="utf-8")), report)
            self.assertTrue(valid, reason)

    def test_generic_backend_staleness(self):
        with tempfile.TemporaryDirectory() as directory:
            from fpga_agent.synth import yosys_command
            if not yosys_command():
                self.skipTest("Yosys required")
            project = self._build(Path(directory), self.COUNTER)
            manifest = json.loads((project / "project.json").read_text(encoding="utf-8"))
            report = run_implement(project)
            self.assertEqual(report["status"], "pass")
            (project / "rtl/counter.v").write_text(
                self.COUNTER.replace("cnt <= 3'd0", "cnt <= 3'd1"), encoding="utf-8", newline="\n")
            valid, reason = implement_valid(project, manifest, report)
            self.assertFalse(valid)
            self.assertIn("changed", reason)


class ImplementIce40IntegrationTests(unittest.TestCase):
    def setUp(self):
        from fpga_agent.ice40 import toolchain
        if toolchain()["reason"]:
            self.skipTest("openfpga toolchain required")

    def test_fixture_implements_and_gate_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            project = _copy_fixture(Path(directory) / "pwm")
            report = run_implement(project)
            self.assertEqual(report["status"], "pass", report["reason"])
            self.assertEqual(report["bitstream"]["extension"], ".bin")
            manifest = json.loads((project / "project.json").read_text(encoding="utf-8"))
            valid, reason = implement_valid(project, manifest, report)
            self.assertTrue(valid, reason)

            from fpga_agent.quality import review_project
            gates = {gate["id"]: gate for gate in review_project(project, "simulation", write=False)["gates"]}
            self.assertEqual(gates["IMPL-003"]["status"], "pass")
            self.assertFalse(gates["IMPL-003"]["required"])

    def test_tampered_bitstream_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            project = _copy_fixture(Path(directory) / "pwm")
            manifest = json.loads((project / "project.json").read_text(encoding="utf-8"))
            report = run_implement(project)
            bitstream = project / report["bitstream"]["path"]
            bitstream.write_bytes(b"tampered")
            valid, reason = implement_valid(project, manifest, report)
            self.assertFalse(valid)
            self.assertIn("modified", reason)


class ImplementReviewGateTests(unittest.TestCase):
    def test_gate_is_not_run_without_a_report(self):
        with tempfile.TemporaryDirectory() as directory:
            project = _copy_fixture(Path(directory) / "pwm")
            from fpga_agent.quality import review_project
            gates = {gate["id"]: gate for gate in review_project(project, "simulation", write=False)["gates"]}
            self.assertIn("IMPL-003", gates)
            self.assertEqual(gates["IMPL-003"]["status"], "not_run")
            self.assertFalse(gates["IMPL-003"]["required"])


class ImplementCliTests(unittest.TestCase):
    def test_implement_subcommand_parses(self):
        args = parser().parse_args(["implement", "examples/design", "--target", "lattice-ice40-hx1k",
                                    "--stage", "full", "--timeout", "600"])
        self.assertEqual(args.command, "implement")
        self.assertEqual((args.target, args.stage, args.timeout), ("lattice-ice40-hx1k", "full", 600))

    def test_targets_subcommand_parses(self):
        args = parser().parse_args(["targets"])
        self.assertEqual(args.command, "targets")


if __name__ == "__main__":
    unittest.main()
