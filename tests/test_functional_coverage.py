from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from fpga_agent.coverage import (
    _assertion_failure,
    _parse_coverage_dat,
    _summarize_functional,
    functional_coverage_valid,
    resolve_coverage_plan,
    run_coverage,
)
from fpga_agent.svsim import _resolve_toolchain

FIXTURE = Path(__file__).resolve().parent.parent / "examples" / "agent_validation" / "functional_cov"

BASE_MANIFEST = {
    "top_module": "dut", "testbench_top": "tb",
    "rtl_sources": ["rtl/a.v"], "testbench_sources": ["tb/b.v"],
}


def _toolchain_ready() -> bool:
    tool = _resolve_toolchain()
    return bool(tool["command"]) and not tool["reason"]


def _copy_fixture(destination: Path) -> Path:
    shutil.copytree(FIXTURE, destination,
                    ignore=shutil.ignore_patterns("build", "reports", "deliverables"))
    return destination


class FunctionalPlanTests(unittest.TestCase):
    def test_functional_flag_defaults_false(self):
        plan = resolve_coverage_plan(BASE_MANIFEST)
        self.assertFalse(plan["functional_coverage"])

    def test_functional_flag_from_manifest(self):
        manifest = dict(BASE_MANIFEST, coverage={"functional_coverage": True})
        self.assertTrue(resolve_coverage_plan(manifest)["functional_coverage"])

    def test_functional_flag_override_true(self):
        self.assertTrue(resolve_coverage_plan(BASE_MANIFEST, functional_coverage=True)["functional_coverage"])

    def test_functional_flag_override_false(self):
        manifest = dict(BASE_MANIFEST, coverage={"functional_coverage": True})
        self.assertFalse(resolve_coverage_plan(manifest, functional_coverage=False)["functional_coverage"])

    def test_functional_flag_rejects_non_bool(self):
        with self.assertRaises(ValueError):
            resolve_coverage_plan(BASE_MANIFEST, functional_coverage="yes")


class FunctionalSummarizeTests(unittest.TestCase):
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

    def test_separates_user_points_from_structural(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            dat = self._write_dat(project, [
                {"file": str(project / "rtl/dut.sv").replace("\\", "/"), "line": 10,
                 "type": "toggle", "object": "q:0->1", "hit": "tb' 5"},
                {"file": str(project / "rtl/dut.sv").replace("\\", "/"), "line": 14,
                 "type": "user", "object": "cover|S14", "hit": "tb' 1"},
                {"file": str(project / "rtl/dut.sv").replace("\\", "/"), "line": 15,
                 "type": "user", "object": "cover|S15", "hit": "tb' 0"},
            ])
            points = _parse_coverage_dat(dat, project)
            functional = _summarize_functional(points, ["rtl/dut.sv"])
            self.assertEqual(functional["total_points"], 2)
            self.assertEqual(functional["total_covered"], 1)
            self.assertEqual(functional["coverage_percent"], 50.0)
            self.assertEqual(len(functional["uncovered_points"]), 1)
            self.assertEqual(functional["uncovered_points"][0]["line"], 15)

    def test_no_functional_points(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            dat = self._write_dat(project, [
                {"file": str(project / "rtl/dut.sv").replace("\\", "/"), "line": 10,
                 "type": "line", "object": "block", "hit": "tb' 1"},
            ])
            points = _parse_coverage_dat(dat, project)
            self.assertEqual(_summarize_functional(points, ["rtl/dut.sv"])["total_points"], 0)


class AssertionDetectionTests(unittest.TestCase):
    def test_detects_assertion_failure(self):
        output = "log\n[185000] %Error: dut.sv:27: Assertion failed in tb.dut: 'assert' failed.\n"
        self.assertIsNotNone(_assertion_failure(output))
        self.assertIn("Assertion failed", _assertion_failure(output))

    def test_no_false_positive(self):
        self.assertIsNone(_assertion_failure("PASS: all checks\n"))


class FunctionalIntegrationTests(unittest.TestCase):
    def setUp(self):
        if not _toolchain_ready():
            self.skipTest("Verilator + C++ compiler + make required")

    def test_fixture_reaches_functional_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            project = _copy_fixture(Path(directory) / "fcov")
            report = run_coverage(project, timeout=600)
            self.assertEqual(report["status"], "pass", report["reason"])
            self.assertEqual(report["structural_status"], "pass")
            self.assertEqual(report["functional_status"], "pass")
            self.assertTrue(report["functional_requested"])
            functional = report["functional"]
            self.assertGreater(functional["total_points"], 0)
            self.assertEqual(functional["total_covered"], functional["total_points"])
            manifest = json.loads((project / "project.json").read_text(encoding="utf-8"))
            valid, reason = functional_coverage_valid(project, manifest, report)
            self.assertTrue(valid, reason)

    def test_failing_assertion_fails_the_run(self):
        with tempfile.TemporaryDirectory() as directory:
            project = _copy_fixture(Path(directory) / "fcov")
            rtl_path = project / "rtl/counter.sv"
            rtl = rtl_path.read_text(encoding="utf-8")
            rtl = rtl.replace("!rst_n |-> cnt == 4'd0", "cnt != 4'd5")
            rtl_path.write_text(rtl, encoding="utf-8")
            report = run_coverage(project, timeout=600)
            self.assertEqual(report["status"], "fail", report["reason"])
            self.assertIsNotNone(report["assertion_failure"])
            self.assertIn("Assertion failed", report["assertion_failure"])


class FunctionalGateTests(unittest.TestCase):
    def test_gate_is_not_run_without_a_report(self):
        with tempfile.TemporaryDirectory() as directory:
            project = _copy_fixture(Path(directory) / "fcov")
            from fpga_agent.quality import review_project
            gates = {gate["id"]: gate for gate in review_project(project, "simulation", write=False)["gates"]}
            self.assertIn("COV-002", gates)
            self.assertEqual(gates["COV-002"]["status"], "not_run")
            self.assertFalse(gates["COV-002"]["required"])

    def test_gate_reports_pass_with_a_functional_report(self):
        if not _toolchain_ready():
            self.skipTest("Verilator + C++ compiler + make required")
        with tempfile.TemporaryDirectory() as directory:
            project = _copy_fixture(Path(directory) / "fcov")
            run_coverage(project, timeout=600)
            from fpga_agent.quality import review_project
            gates = {gate["id"]: gate for gate in review_project(project, "simulation", write=False)["gates"]}
            self.assertEqual(gates["COV-002"]["status"], "pass")


class FunctionalCliTests(unittest.TestCase):
    def test_functional_flag_parses(self):
        from fpga_agent.cli import parser
        args = parser().parse_args(["coverage", "examples/design", "--functional-coverage"])
        self.assertEqual(args.command, "coverage")
        self.assertTrue(args.functional_coverage)

    def test_functional_flag_default_is_none(self):
        from fpga_agent.cli import parser
        args = parser().parse_args(["coverage", "examples/design"])
        self.assertIsNone(args.functional_coverage)


if __name__ == "__main__":
    unittest.main()
