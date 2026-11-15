from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fpga_agent.quality import review_project
from fpga_agent.svsim import (
    _finalize_status,
    _resolve_toolchain,
    resolve_svsim_plan,
    run_svsim,
    svsim_valid,
)
from fpga_agent.verify import verify_project

FIXTURE = Path(__file__).resolve().parent.parent / "examples" / "agent_validation" / "sv_class_env"


def _toolchain_ready() -> bool:
    tool = _resolve_toolchain()
    return bool(tool["command"]) and not tool["reason"]


def _copy_fixture(destination: Path) -> Path:
    shutil.copytree(FIXTURE, destination, ignore=shutil.ignore_patterns("build", "reports"))
    return destination


class SvsimPlanTests(unittest.TestCase):
    def test_requires_top(self):
        with self.assertRaises(ValueError):
            resolve_svsim_plan({"svsim": {"sources": ["rtl/a.sv"]}})

    def test_requires_sources(self):
        with self.assertRaises(ValueError):
            resolve_svsim_plan({"svsim": {"top": "tb"}})

    def test_reads_manifest_block(self):
        manifest = {"svsim": {"top": "tb", "sources": ["rtl/a.sv", "verif/b.sv"]}}
        plan = resolve_svsim_plan(manifest)
        self.assertEqual(plan["top"], "tb")
        self.assertEqual(plan["sources"], ["rtl/a.sv", "verif/b.sv"])
        self.assertEqual(plan["cflags"], "-O2")

    def test_explicit_arguments_override_manifest(self):
        manifest = {"svsim": {"top": "tb", "sources": ["rtl/a.sv"]}}
        plan = resolve_svsim_plan(manifest, top="other", sources=["verif/c.sv"], cflags="-O1")
        self.assertEqual(plan["top"], "other")
        self.assertEqual(plan["sources"], ["verif/c.sv"])
        self.assertEqual(plan["cflags"], "-O1")


class SvsimStatusTests(unittest.TestCase):
    def _report(self, output_tail: str, checks: list[dict] | None = None) -> dict:
        return {"checks": checks if checks is not None else [{"id": "REQ-001", "status": "pass"}],
                "output_tail": output_tail, "min_checks": 1}

    def test_clean_run_passes(self):
        report = self._report("CHECK REQ-001 PASS\nPASS: env\n")
        _finalize_status(report, 0)
        self.assertEqual(report["status"], "pass")

    def test_fail_line_fails(self):
        report = self._report("FAIL: mismatch\nPASS: env\n")
        _finalize_status(report, 0)
        self.assertEqual(report["status"], "fail")

    def test_missing_pass_marker_fails(self):
        report = self._report("CHECK REQ-001 PASS\n")
        _finalize_status(report, 0)
        self.assertEqual(report["status"], "fail")

    def test_nonzero_returncode_fails(self):
        report = self._report("PASS: env\n")
        _finalize_status(report, 3)
        self.assertEqual(report["status"], "fail")

    def test_plan_error_fails(self):
        report = self._report("PASS: env\n")
        report["plan_error"] = "CHECK markers do not match the verification plan"
        _finalize_status(report, 0)
        self.assertEqual(report["status"], "fail")


class SvsimBlockedTests(unittest.TestCase):
    def test_missing_toolchain_is_blocked_not_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            project = _copy_fixture(Path(directory) / "env")
            with patch("fpga_agent.svsim._resolve_toolchain",
                       return_value={"command": [], "version": None, "cxx": None, "make": None,
                                     "env": {}, "reason": "Verilator is not installed or not on PATH."}):
                report = run_svsim(project)
            self.assertEqual(report["status"], "blocked")
            self.assertEqual(report["kind"], "svsim")
            self.assertTrue((project / "reports/svsim.json").is_file())
            ok, reason = svsim_valid(project, json.loads((project / "project.json").read_text(encoding="utf-8")), report)
            self.assertFalse(ok)


class SvsimFixtureTests(unittest.TestCase):
    """The pinned fixture must pass both verification paths from one RTL source."""

    def test_fixture_declares_class_environment(self):
        manifest = json.loads((FIXTURE / "project.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["svsim"]["top"], "tb_alu8_env")
        for name in manifest["svsim"]["sources"]:
            self.assertTrue((FIXTURE / name).is_file(), name)
        self.assertTrue((FIXTURE / "spec/verification-plan.json").is_file())

    def test_icarus_self_checking_simulation_passes(self):
        report = verify_project(FIXTURE)
        self.assertEqual(report.status, "pass", report.output[-2000:])
        self.assertTrue(report.checks and all(item["status"] == "pass" for item in report.checks))

    def test_class_environment_passes_and_is_current(self):
        if not _toolchain_ready():
            self.skipTest("Verilator --binary toolchain (Verilator + C++ + make) required")
        report = run_svsim(FIXTURE)
        self.assertEqual(report["status"], "pass", report.get("reason"))
        self.assertTrue(report["checks"] and all(item["status"] == "pass" for item in report["checks"]))
        self.assertFalse(report.get("plan_error"))
        manifest = json.loads((FIXTURE / "project.json").read_text(encoding="utf-8"))
        ok, reason = svsim_valid(FIXTURE, manifest, report)
        self.assertTrue(ok, reason)


class SvsimStalenessTests(unittest.TestCase):
    def test_editing_a_source_invalidates_the_report(self):
        if not _toolchain_ready():
            self.skipTest("Verilator --binary toolchain (Verilator + C++ + make) required")
        with tempfile.TemporaryDirectory() as directory:
            project = _copy_fixture(Path(directory) / "env")
            report = run_svsim(project)
            self.assertEqual(report["status"], "pass", report.get("reason"))
            manifest = json.loads((project / "project.json").read_text(encoding="utf-8"))
            self.assertTrue(svsim_valid(project, manifest, report)[0])
            source = project / "rtl/alu8.sv"
            source.write_text(source.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            ok, reason = svsim_valid(project, manifest, report)
            self.assertFalse(ok)
            self.assertIn("changed", reason.lower())


class SvsimReviewGateTests(unittest.TestCase):
    def test_gate_is_not_run_without_a_report(self):
        with tempfile.TemporaryDirectory() as directory:
            project = _copy_fixture(Path(directory) / "env")
            review = review_project(project, "simulation", write=False)
            gates = {gate["id"]: gate for gate in review["gates"]}
            self.assertIn("DV-008", gates)
            self.assertEqual(gates["DV-008"]["status"], "not_run")
            self.assertFalse(gates["DV-008"]["required"])

    def test_gate_passes_with_a_current_report(self):
        if not _toolchain_ready():
            self.skipTest("Verilator --binary toolchain (Verilator + C++ + make) required")
        run_svsim(FIXTURE)
        review = review_project(FIXTURE, "simulation", write=False)
        gates = {gate["id"]: gate for gate in review["gates"]}
        self.assertEqual(gates["DV-008"]["status"], "pass", gates["DV-008"]["detail"])


if __name__ == "__main__":
    unittest.main()
