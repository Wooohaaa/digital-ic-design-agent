from __future__ import annotations

import json
import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fpga_agent.verify import verify_project


class VerifyProjectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name) / "project"
        (self.project / "rtl").mkdir(parents=True)
        (self.project / "tb").mkdir()
        (self.project / "rtl" / "core.v").write_text("module core; endmodule\n", encoding="utf-8")
        (self.project / "tb" / "tb_core.v").write_text(
            "module tb_core; initial begin $display(\"PASS\"); end endmodule\n", encoding="utf-8"
        )
        self.manifest = {
            "top_module": "core",
            "testbench_top": "tb_core",
            "rtl_sources": ["rtl/core.v"],
            "testbench_sources": ["tb/tb_core.v"],
        }
        (self.project / "project.json").write_text(json.dumps(self.manifest), encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_timeout_with_bytes_is_normalized_and_reported(self) -> None:
        with patch("fpga_agent.verify.shutil.which", side_effect=["iverilog", "vvp"]), patch(
            "fpga_agent.verify.subprocess.run",
            side_effect=subprocess.TimeoutExpired("iverilog", 1, output=b"partial \xff", stderr=b"compile err"),
        ):
            result = verify_project(self.project, timeout=1)
        self.assertEqual(result.status, "fail")
        self.assertEqual(result.returncode, 124)
        self.assertIn("partial ", result.output)
        self.assertIn("compile err", result.output)
        self.assertIn("仿真超时", result.output)
        self.assertTrue((self.project / "reports" / "simulation.json").is_file())

    def test_source_mutation_between_compile_and_run_forces_fail_with_start_hashes(self) -> None:
        original_hash = hashlib.sha256((self.project / "rtl" / "core.v").read_bytes()).hexdigest()
        calls = 0

        def compile_then_mutate(command, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            (self.project / "rtl" / "core.v").write_text("module core; wire changed; endmodule\n", encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="PASS\n", stderr="")

        with patch("fpga_agent.verify.shutil.which", side_effect=["iverilog", "vvp"]), patch(
            "fpga_agent.verify.subprocess.run", side_effect=compile_then_mutate
        ):
            result = verify_project(self.project)

        self.assertEqual(result.status, "fail")
        self.assertIn("inputs changed while simulation was running", result.output)
        self.assertEqual(result.source_hashes["rtl/core.v"], original_hash)
        self.assertNotEqual(hashlib.sha256((self.project / "rtl" / "core.v").read_bytes()).hexdigest(), original_hash)

    def test_riscv_dispatch_returns_subset_without_replacing_detailed_report(self) -> None:
        self.manifest["verification"] = {"kind": "riscv-retire", "seeds": 7}
        (self.project / "project.json").write_text(json.dumps(self.manifest), encoding="utf-8")
        report_dir = self.project / "reports"
        report_dir.mkdir()
        detailed_report = report_dir / "simulation.json"
        detailed_report.write_text("detailed cpu bench report", encoding="utf-8")
        report = {
            "status": "pass",
            "simulator": "Icarus Verilog",
            "command": ["iverilog", "-g2012"],
            "output": "RV32I pass",
            "started_at": "2026-11-04T00:00:00+00:00",
            "elapsed_seconds": 1.25,
            "source_hashes": {"rtl/core.v": "abc"},
            "case_count": 44,
        }
        with patch("fpga_agent.cpu_bench.run_benchmark", return_value=report) as run_benchmark:
            result = verify_project(self.project)
        run_benchmark.assert_called_once_with(self.project, seeds=7)
        self.assertEqual(result.status, "pass")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.source_hashes, {"rtl/core.v": "abc"})
        self.assertEqual(detailed_report.read_text(encoding="utf-8"), "detailed cpu bench report")


if __name__ == "__main__":
    unittest.main()
