from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fpga_agent.coverage import (
    _parse_coverage_dat,
    _summarize,
    coverage_valid,
    resolve_coverage_plan,
    run_coverage,
)
from fpga_agent.svsim import _resolve_toolchain

FIXTURE = Path(__file__).resolve().parent.parent / "examples" / "agent_validation" / "ice40_pwm"

ICE_MANIFEST = {
    "project": "pwm", "top_module": "pwm_breath", "testbench_top": "tb_pwm_breath",
    "rtl_sources": ["rtl/pwm_breath.v"], "testbench_sources": ["tb/tb_pwm_breath.v"],
}


def _toolchain_ready() -> bool:
    tool = _resolve_toolchain()
    return bool(tool["command"]) and not tool["reason"]


def _copy_fixture(destination: Path) -> Path:
    shutil.copytree(FIXTURE, destination, ignore=shutil.ignore_patterns("build", "reports", "deliverables"))
    return destination


class CoveragePlanTests(unittest.TestCase):
    def test_resolves_from_manifest(self):
        plan = resolve_coverage_plan(ICE_MANIFEST)
        self.assertEqual(plan["top"], "pwm_breath")
        self.assertEqual(plan["testbench_top"], "tb_pwm_breath")
        self.assertEqual(plan["sources"], ["rtl/pwm_breath.v"])
        self.assertEqual(plan["testbench_sources"], ["tb/tb_pwm_breath.v"])

    def test_requires_rtl_top(self):
        with self.assertRaises(ValueError):
            resolve_coverage_plan({"rtl_sources": ["rtl/a.v"], "testbench_top": "tb"})

    def test_requires_testbench_top(self):
        with self.assertRaises(ValueError):
            resolve_coverage_plan({"top_module": "dut", "rtl_sources": ["rtl/a.v"]})

    def test_requires_rtl_sources(self):
        with self.assertRaises(ValueError):
            resolve_coverage_plan({"top_module": "dut", "testbench_top": "tb",
                                   "testbench_sources": ["tb/b.v"]})

    def test_requires_testbench_sources(self):
        with self.assertRaises(ValueError):
            resolve_coverage_plan({"top_module": "dut", "testbench_top": "tb",
                                   "rtl_sources": ["rtl/a.v"]})


class CoverageParseTests(unittest.TestCase):
    def _write_dat(self, tmp: Path, points: list[dict]) -> Path:
        lines = ["# SystemC::Coverage-3"]
        for p in points:
            body = "\x01".join([
                f"f\x02{p['file']}", f"l\x02{p['line']}", f"n\x021",
                f"t\x02{p['type']}", f"page\x02v_{p['type']}/dut",
                f"o\x02{p['object']}", f"h\x02{p['hit']}",
            ])
            lines.append(f"C '{body}")
        dat = tmp / "coverage.dat"
        dat.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return dat

    def test_parses_all_points_and_summary_filters_testbench(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            dat = self._write_dat(project, [
                {"file": str(project / "rtl/dut.v").replace("\\", "/"), "line": 10,
                 "type": "toggle", "object": "q:0->1", "hit": "tb' 5"},
                {"file": str(project / "rtl/dut.v").replace("\\", "/"), "line": 11,
                 "type": "line", "object": "block", "hit": "tb' 3"},
                {"file": str(project / "tb/tb_dut.v").replace("\\", "/"), "line": 5,
                 "type": "line", "object": "block", "hit": "tb' 1"},
            ])
            points = _parse_coverage_dat(dat, project)
            self.assertEqual({p["basename"] for p in points}, {"dut.v", "tb_dut.v"})
            types = {p["type"] for p in points if p["basename"] == "dut.v"}
            self.assertEqual(types, {"toggle", "line"})
            # The summary only counts points whose basename matches an RTL source.
            summary = _summarize(points, ["rtl/dut.v"])
            self.assertEqual(list(summary["by_source"]), ["rtl/dut.v"])
            self.assertEqual(summary["by_source"]["rtl/dut.v"]["points"], 2)

    def test_hit_count_zero_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            dat = self._write_dat(project, [
                {"file": str(project / "rtl/dut.v").replace("\\", "/"), "line": 10,
                 "type": "line", "object": "block", "hit": "tb' 0"},
            ])
            points = _parse_coverage_dat(dat, project)
            self.assertEqual(points[0]["hits"], 0)

    def test_summary_counts_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            dat = self._write_dat(project, [
                {"file": str(project / "rtl/dut.v").replace("\\", "/"), "line": 10,
                 "type": "toggle", "object": "a:0->1", "hit": "tb' 5"},
                {"file": str(project / "rtl/dut.v").replace("\\", "/"), "line": 11,
                 "type": "toggle", "object": "b:0->1", "hit": "tb' 0"},
            ])
            points = _parse_coverage_dat(dat, project)
            summary = _summarize(points, ["rtl/dut.v"])
            entry = summary["by_source"]["rtl/dut.v"]
            self.assertEqual(entry["points"], 2)
            self.assertEqual(entry["covered"], 1)
            self.assertEqual(summary["coverage_percent"], 50.0)


class CoverageIntegrationTests(unittest.TestCase):
    """The pinned fixture must reach complete structural coverage when tools exist."""

    def setUp(self):
        if not _toolchain_ready():
            self.skipTest("Verilator + C++ compiler + make required")

    def test_fixture_reaches_complete_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            project = _copy_fixture(Path(directory) / "pwm")
            report = run_coverage(project, timeout=600)
            self.assertEqual(report["status"], "pass", report["reason"])
            self.assertEqual(report["kind"], "coverage")
            coverage = report["coverage"]
            self.assertGreater(coverage["total_points"], 0)
            self.assertEqual(coverage["total_covered"], coverage["total_points"])
            self.assertEqual(coverage["coverage_percent"], 100.0)
            manifest = json.loads((project / "project.json").read_text(encoding="utf-8"))
            valid, reason = coverage_valid(project, manifest, report)
            self.assertTrue(valid, reason)
            self.assertTrue((project / "reports/coverage.json").is_file())

    def test_stale_rtl_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            project = _copy_fixture(Path(directory) / "pwm")
            report = run_coverage(project, timeout=600)
            manifest = json.loads((project / "project.json").read_text(encoding="utf-8"))
            # Append a whitespace-only change so the file bytes (and hash) differ.
            original = (project / "rtl/pwm_breath.v").read_text(encoding="utf-8")
            (project / "rtl/pwm_breath.v").write_text(original + "\n", encoding="utf-8")
            valid, reason = coverage_valid(project, manifest, report)
            self.assertFalse(valid)
            self.assertIn("changed", reason)


class CoverageBlockedTests(unittest.TestCase):
    def test_missing_toolchain_is_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            project = _copy_fixture(Path(directory) / "pwm")
            with patch("fpga_agent.coverage._resolve_toolchain",
                       return_value={"command": [], "version": None, "cxx": None,
                                     "make": None, "env": {}, "reason": "Verilator missing"}):
                report = run_coverage(project, timeout=120)
            self.assertEqual(report["status"], "blocked")
            self.assertEqual(report["kind"], "coverage")
            self.assertTrue((project / "reports/coverage.json").is_file())
            manifest = json.loads((project / "project.json").read_text(encoding="utf-8"))
            valid, reason = coverage_valid(project, manifest, report)
            self.assertFalse(valid)
            self.assertIn("blocked", reason)


class CoverageReviewGateTests(unittest.TestCase):
    def test_gate_is_not_run_without_a_report(self):
        with tempfile.TemporaryDirectory() as directory:
            project = _copy_fixture(Path(directory) / "pwm")
            from fpga_agent.quality import review_project
            gates = {gate["id"]: gate for gate in review_project(project, "simulation", write=False)["gates"]}
            self.assertIn("COV-001", gates)
            self.assertEqual(gates["COV-001"]["status"], "not_run")
            self.assertFalse(gates["COV-001"]["required"])


class CoverageCliTests(unittest.TestCase):
    def test_coverage_subcommand_parses(self):
        from fpga_agent.cli import parser
        args = parser().parse_args(["coverage", "examples/design", "--top", "dut",
                                    "--testbench-top", "tb_dut", "--timeout", "300"])
        self.assertEqual(args.command, "coverage")
        self.assertEqual((args.top, args.testbench_top, args.timeout), ("dut", "tb_dut", 300))


if __name__ == "__main__":
    unittest.main()
