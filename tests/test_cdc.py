from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from fpga_agent.cdc import analyze_cdc, cdc_valid, run_cdc
from fpga_agent.quality import review_project

FIXTURE_DUAL = Path(__file__).resolve().parent.parent / "examples" / "agent_validation" / "cdc_dual_clk"
FIXTURE_SINGLE = Path(__file__).resolve().parent.parent / "examples" / "agent_validation" / "ice40_pwm"

DUAL_CLOCK = """
module dual (
  input logic clk_a, input logic clk_b, input logic rst_n,
  input logic ping, output logic pong
);
  logic a_reg;
  always_ff @(posedge clk_a or negedge rst_n) begin
    if (!rst_n) a_reg <= 1'b0;
    else a_reg <= ping;
  end
  always_ff @(posedge clk_b or negedge rst_n) begin
    if (!rst_n) pong <= 1'b0;
    else pong <= a_reg;
  end
endmodule
"""

SINGLE_CLOCK = """
module single (
  input logic clk, input logic rst_n, input logic d, output logic q
);
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) q <= 1'b0;
    else q <= d;
  end
endmodule
"""


def _make_project(directory: Path, name: str, rtl: str, *, sources=None) -> Path:
    project = directory / name
    (project / "rtl").mkdir(parents=True)
    (project / "rtl" / f"{name}.v").write_text(rtl, encoding="utf-8")
    manifest = {"project": name, "top_module": name,
                "rtl_sources": sources if sources is not None else [f"rtl/{name}.v"]}
    (project / "project.json").write_text(json.dumps(manifest), encoding="utf-8")
    return project


class CdcAnalysisTests(unittest.TestCase):
    def test_dual_clock_crossing_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            project = _make_project(Path(directory), "dual", DUAL_CLOCK)
            analysis = analyze_cdc(project, ["rtl/dual.v"])
            self.assertEqual(sorted(analysis["clocks"]), ["clk_a", "clk_b"])
            self.assertEqual(len(analysis["crossings"]), 1)
            crossing = analysis["crossings"][0]
            self.assertEqual(crossing["signal"], "a_reg")
            self.assertEqual(crossing["from_clock"], "clk_a")
            self.assertEqual(crossing["to_clock"], "clk_b")

    def test_reset_is_not_a_clock(self):
        with tempfile.TemporaryDirectory() as directory:
            project = _make_project(Path(directory), "dual", DUAL_CLOCK)
            analysis = analyze_cdc(project, ["rtl/dual.v"])
            self.assertNotIn("rst_n", analysis["clocks"])
            self.assertTrue(analysis["reset_domains"])

    def test_single_clock_has_no_crossing(self):
        with tempfile.TemporaryDirectory() as directory:
            project = _make_project(Path(directory), "single", SINGLE_CLOCK)
            analysis = analyze_cdc(project, ["rtl/single.v"])
            self.assertEqual(analysis["crossings"], [])
            self.assertEqual(analysis["clocks"], ["clk"])


class CdcRunTests(unittest.TestCase):
    def test_dual_clock_fixture_is_review_required(self):
        with tempfile.TemporaryDirectory() as directory:
            project = _copy(FIXTURE_DUAL, Path(directory) / "dual")
            report = run_cdc(project, timeout=30)
            self.assertEqual(report["status"], "review_required")
            self.assertEqual(report["kind"], "cdc")
            self.assertTrue(report["crossings"])
            self.assertTrue((project / "reports/cdc.json").is_file())
            manifest = json.loads((project / "project.json").read_text(encoding="utf-8"))
            valid, reason = cdc_valid(project, manifest, report)
            self.assertFalse(valid)
            self.assertIn("review", reason)

    def test_single_clock_fixture_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            project = _copy(FIXTURE_SINGLE, Path(directory) / "single")
            report = run_cdc(project, timeout=30)
            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["crossings"], [])
            manifest = json.loads((project / "project.json").read_text(encoding="utf-8"))
            valid, reason = cdc_valid(project, manifest, report)
            self.assertTrue(valid, reason)

    def test_missing_rtl_sources_is_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            project = _make_project(Path(directory), "empty", SINGLE_CLOCK, sources=[])
            report = run_cdc(project, timeout=30)
            self.assertEqual(report["status"], "blocked")
            self.assertEqual(report["kind"], "cdc")

    def test_stale_rtl_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            project = _copy(FIXTURE_DUAL, Path(directory) / "dual")
            report = run_cdc(project, timeout=30)
            manifest = json.loads((project / "project.json").read_text(encoding="utf-8"))
            original = (project / "rtl/dual_clk.v").read_text(encoding="utf-8")
            (project / "rtl/dual_clk.v").write_text(original + "\n", encoding="utf-8")
            valid, reason = cdc_valid(project, manifest, report)
            self.assertFalse(valid)
            self.assertIn("changed", reason)


class CdcGateTests(unittest.TestCase):
    def test_gate_is_not_run_without_a_report(self):
        with tempfile.TemporaryDirectory() as directory:
            project = _copy(FIXTURE_DUAL, Path(directory) / "dual")
            gates = {gate["id"]: gate
                     for gate in review_project(project, "simulation", write=False)["gates"]}
            self.assertIn("CDC-001", gates)
            self.assertEqual(gates["CDC-001"]["status"], "not_run")
            self.assertFalse(gates["CDC-001"]["required"])

    def test_gate_reports_review_required_with_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            project = _copy(FIXTURE_DUAL, Path(directory) / "dual")
            run_cdc(project, timeout=30)
            gates = {gate["id"]: gate
                     for gate in review_project(project, "simulation", write=False)["gates"]}
            self.assertEqual(gates["CDC-001"]["status"], "review_required")
            self.assertFalse(gates["CDC-001"]["required"])


class CdcCliTests(unittest.TestCase):
    def test_cdc_subcommand_parses(self):
        from fpga_agent.cli import parser
        args = parser().parse_args(["cdc", "examples/design", "--timeout", "30"])
        self.assertEqual(args.command, "cdc")
        self.assertEqual(args.timeout, 30)


def _copy(source: Path, destination: Path) -> Path:
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns("build", "reports", "deliverables"))
    return destination


if __name__ == "__main__":
    unittest.main()
