from __future__ import annotations

import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from fpga_agent.cli import do_package
from fpga_agent.release import DEFAULT_VERSION, run_release

FIXTURE = Path(__file__).resolve().parent.parent / "examples" / "agent_validation" / "ice40_pwm"


def _copy_fixture(destination: Path) -> Path:
    shutil.copytree(FIXTURE, destination,
                    ignore=shutil.ignore_patterns("build", "reports", "deliverables"))
    return destination


class ReleaseManifestTests(unittest.TestCase):
    def test_run_release_writes_manifest_with_gates(self):
        with tempfile.TemporaryDirectory() as directory:
            project = _copy_fixture(Path(directory) / "pwm")
            release = run_release(project, version="1.2.3")
            self.assertEqual(release["kind"], "release")
            self.assertEqual(release["version"], "1.2.3")
            self.assertIn("aggregate", release)
            self.assertIn("inputs", release)
            report = project / "reports/release.json"
            self.assertTrue(report.is_file())
            self.assertEqual(json.loads(report.read_text(encoding="utf-8")), release)
            # Stage 4/5/6 gates are aggregated with the earlier gates.
            for gate in ("SPEC-001", "DV-001", "CDC-001", "COV-001", "COV-002", "TIM-001", "SYNTH-001"):
                self.assertIn(gate, release["gates"])
                self.assertIn("status", release["gates"][gate])

    def test_release_version_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            project = _copy_fixture(Path(directory) / "pwm")
            self.assertEqual(run_release(project)["version"], DEFAULT_VERSION)

    def test_aggregate_reports_signoff_not_granted(self):
        with tempfile.TemporaryDirectory() as directory:
            project = _copy_fixture(Path(directory) / "pwm")
            release = run_release(project)
            self.assertEqual(release["aggregate"]["engineering_signoff"], "not_granted")
            self.assertIn("artifact_ready", release["aggregate"])


class ReleasePackageTests(unittest.TestCase):
    def test_versioned_package_name_and_delivery_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            project = _copy_fixture(Path(directory) / "pwm")
            (project / "reports").mkdir(exist_ok=True)
            with patch("fpga_agent.cli.simulation_valid", return_value=(True, "pass")), \
                 patch("fpga_agent.implement.implement_valid", return_value=(True, "pass")), \
                 patch("fpga_agent.quality.review_project",
                       return_value={"artifact_ready": True, "engineering_signoff": "not_granted",
                                     "required_open_items": 0}):
                code = do_package(project, version="1.2.3")
            self.assertEqual(code, 0)
            artifact = project / "deliverables/validation-ice40-pwm-delivery-1.2.3.zip"
            self.assertTrue(artifact.is_file())
            with zipfile.ZipFile(artifact) as archive:
                delivery = json.loads(archive.read("delivery.json"))
                self.assertEqual(delivery["release_version"], "1.2.3")
                self.assertEqual(delivery["engineering_signoff"], "not_granted")

    def test_default_package_name_is_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            project = _copy_fixture(Path(directory) / "pwm")
            (project / "reports").mkdir(exist_ok=True)
            with patch("fpga_agent.cli.simulation_valid", return_value=(True, "pass")), \
                 patch("fpga_agent.implement.implement_valid", return_value=(True, "pass")), \
                 patch("fpga_agent.quality.review_project",
                       return_value={"artifact_ready": True, "engineering_signoff": "not_granted",
                                     "required_open_items": 0}):
                code = do_package(project)
            self.assertEqual(code, 0)
            self.assertTrue((project / "deliverables/validation-ice40-pwm-delivery.zip").is_file())


class ReleaseCliTests(unittest.TestCase):
    def test_release_subcommand_parses(self):
        from fpga_agent.cli import parser
        args = parser().parse_args(["release", "examples/design", "--version", "2.0.0"])
        self.assertEqual(args.command, "release")
        self.assertEqual(args.version, "2.0.0")


class ReleaseEndToEndTests(unittest.TestCase):
    """Local pipeline end to end: verify + cdc + timing + aggregate release."""

    def _simulator_ready(self) -> bool:
        if shutil.which("iverilog"):
            return True
        return (Path(__file__).resolve().parents[1] / ".tools/icarus/bin/iverilog.exe").exists()

    def test_local_pipeline_release(self):
        if not self._simulator_ready():
            self.skipTest("Icarus simulator required")
        from fpga_agent.verify import verify_project
        from fpga_agent.cdc import run_cdc
        from fpga_agent.timing import run_timing
        with tempfile.TemporaryDirectory() as directory:
            project = _copy_fixture(Path(directory) / "pwm")
            _reports = project / "reports"
            _reports.mkdir(exist_ok=True)
            # Stage the cached implementation report/bitstream so timing can run.
            shutil.copy(FIXTURE / "reports/implement.json", _reports / "implement.json")
            build = project / "build" / "implement"
            build.mkdir(parents=True, exist_ok=True)
            for name in ("pwm_breath.bin", "pwm_breath.asc", "pwm_breath.json", "ice40.ys"):
                source = FIXTURE / "build" / "implement" / name
                if source.is_file():
                    shutil.copy(source, build / name)
            self.assertEqual(verify_project(project).status, "pass")
            self.assertEqual(run_cdc(project, timeout=30)["status"], "pass")
            run_timing(project, timeout=30)
            release = run_release(project, version="0.7.0")
            self.assertEqual(release["version"], "0.7.0")
            self.assertEqual(release["gates"]["DV-001"]["status"], "pass")
            self.assertEqual(release["gates"]["CDC-001"]["status"], "pass")
            self.assertEqual(release["aggregate"]["engineering_signoff"], "not_granted")


if __name__ == "__main__":
    unittest.main()
