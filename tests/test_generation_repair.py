from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fpga_agent import cli, cpu_agent
from fpga_agent.project import create_project


def valid_generic_payload() -> dict:
    return {
        "top_module": "demo",
        "testbench_top": "tb_demo",
        "board_io": {},
        "design_spec": "Minimal generic verification design.",
        "files": {
            "rtl/demo.v": "module demo(input wire clk); endmodule",
            "tb/tb_demo.v": "module tb_demo; initial begin assert (1'b1); end endmodule",
        },
        "checks": ["testbench assertion executes"],
        "_generation": {"provider": "api", "model": "gpt-6-luna"},
    }


def make_cpu_project(path: Path) -> Path:
    path.mkdir(parents=True)
    (path / "rtl").mkdir()
    (path / "spec").mkdir()
    (path / "generation").mkdir()
    core = path / "rtl" / "rv32_core.v"
    core.write_text("// existing protected core\nmodule rv32_core; endmodule\n", encoding="utf-8")
    (path / "spec" / "design.md").write_text("existing design notes\n", encoding="utf-8")
    return path


class GenerationRepairTests(unittest.TestCase):
    def test_generic_api_validation_failure_retries_with_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = create_project("GenericRetry", Path(directory) / "generic")
            invalid = {"top_module": "bad name"}
            generated = [invalid, valid_generic_payload()]
            verifier_result = SimpleNamespace(status="pass", output="PASS\n", elapsed_seconds=0.01)

            with patch.object(cli, "generate_payload", side_effect=generated) as generate_mock, patch.object(
                cli, "verify_project", return_value=verifier_result
            ):
                result = cli.do_design(project, "design brief", repair_rounds=1, provider="api", model="gpt-6-luna")

            self.assertEqual(result, 0)
            self.assertEqual(generate_mock.call_count, 2)
            second_call = generate_mock.call_args_list[1].kwargs
            self.assertIn("Candidate validation failed before commit", second_call["diagnostics"])
            self.assertIn("top_module", second_call["diagnostics"])
            self.assertTrue((project / "rtl" / "demo.v").is_file())
            self.assertTrue((project / "generation" / "round_00" / "validation-error.txt").is_file())

    def test_exchange_cpu_invalid_response_preserves_core_and_requests_repair(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = make_cpu_project(Path(directory) / "cpu_exchange")
            core = project / "rtl" / "rv32_core.v"
            original = core.read_bytes()
            invalid = {"rtl": "module rv32_core(input wire clk); endmodule", "design_spec": "invalid interface"}

            with patch.object(cpu_agent, "request_json", return_value=invalid) as request_mock:
                report = cpu_agent.design_cpu(project, "single", provider="exchange", rounds=3)

            self.assertEqual(report["status"], "fail")
            self.assertEqual(request_mock.call_count, 1)
            self.assertEqual(core.read_bytes(), original)
            repair_request = project / "generation" / "repair-request.txt"
            self.assertTrue(repair_request.is_file())
            self.assertIn("Candidate validation failed before commit", repair_request.read_text(encoding="utf-8"))

    def test_api_cpu_invalid_response_uses_bounded_rounds_without_mutating_core(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = make_cpu_project(Path(directory) / "cpu_api")
            core = project / "rtl" / "rv32_core.v"
            original = core.read_bytes()
            invalid = {"rtl": "module rv32_core(input wire clk); endmodule", "design_spec": "invalid interface"}

            with patch.object(cpu_agent, "request_json", return_value=invalid) as request_mock:
                report = cpu_agent.design_cpu(project, "single", provider="api", rounds=2)

            self.assertEqual(report["status"], "fail")
            self.assertEqual(request_mock.call_count, 3)
            self.assertEqual(core.read_bytes(), original)
            self.assertFalse((project / "generation" / "repair-request.txt").exists())
            for attempt in range(3):
                self.assertTrue((project / "generation" / f"design_round_{attempt:02}" / "validation-error.txt").is_file())


if __name__ == "__main__":
    unittest.main()
