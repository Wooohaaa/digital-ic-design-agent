from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import os

from fpga_agent.project import _ansi_top_ports, write_model_artifacts


class WriteModelArtifactsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name) / "project"
        (self.project / "board").mkdir(parents=True)
        for directory in ("rtl", "tb", "spec", "constraints"):
            (self.project / directory).mkdir()
        (self.project / "project.json").write_text(
            json.dumps({"project": "test", "top_module": "old_top"}), encoding="utf-8"
        )
        (self.project / "board" / "hx1p35a.json").write_text(
            json.dumps(
                {
                    "signals": {
                        "CLK": {"pin": "C4", "direction": "input", "io_standard": "LVCMOS33"},
                        "LED": {"pin": "R11", "direction": "output", "io_standard": "LVCMOS33"},
                        "ALT": {"pin": "R11", "direction": "input", "io_standard": "LVCMOS33"},
                    }
                }
            ),
            encoding="utf-8",
        )
        (self.project / "rtl" / "old.v").write_text("old rtl\n", encoding="utf-8")
        (self.project / "tb" / "old.v").write_text("old tb\n", encoding="utf-8")
        (self.project / "spec" / "design.md").write_text("old design\n", encoding="utf-8")
        (self.project / "constraints" / "generated.adc").write_text("old constraints\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def payload(self) -> dict:
        return {
            "top_module": "top",
            "testbench_top": "tb_top",
            "files": {
                "rtl/top.v": "module top(input wire clk, output wire led); endmodule",
                "tb/tb_top.v": "module tb_top; initial begin $fatal(1, \"self check\"); end endmodule",
            },
            "design_spec": "A short test design.",
            "board_io": {"clk": "CLK", "led": "LED"},
            "checks": ["reset behavior"],
        }

    def snapshot(self) -> dict[str, bytes]:
        return {
            str(path.relative_to(self.project)): path.read_bytes()
            for path in self.project.rglob("*")
            if path.is_file()
        }

    def test_malformed_payload_does_not_modify_any_project_files(self) -> None:
        before = self.snapshot()
        payload = self.payload()
        payload["design_spec"] = 42  # Rejected after all HDL checks in the old write path.
        with self.assertRaises(ValueError):
            write_model_artifacts(self.project, payload)
        self.assertEqual(self.snapshot(), before)

    def test_duplicate_board_pin_binding_is_rejected_without_writes(self) -> None:
        before = self.snapshot()
        payload = self.payload()
        payload["files"]["rtl/top.v"] = "module top(input wire clk, input wire alt, output wire led); endmodule"
        payload["board_io"] = {"clk": "CLK", "alt": "ALT", "led": "LED"}
        with self.assertRaisesRegex(ValueError, "引脚"):
            write_model_artifacts(self.project, payload)
        self.assertEqual(self.snapshot(), before)

    def test_ansi_parser_inherits_packed_vector_declaration_and_strips_comments(self) -> None:
        source = """
            // module top(input fake);
            module top(input wire [31:0] a, b, output logic ready);
                /* ignored, module fake; */
            endmodule
        """
        ports, vectors = _ansi_top_ports(source, "top")
        self.assertEqual(ports, {"a": "input", "b": "input", "ready": "output"})
        self.assertEqual(vectors, {"a", "b"})

    def test_normal_payload_is_accepted_and_committed(self) -> None:
        write_model_artifacts(self.project, self.payload())
        manifest = json.loads((self.project / "project.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["top_module"], "top")
        self.assertEqual(manifest["top_ports"], {"clk": "input", "led": "output"})
        self.assertEqual(manifest["unmapped_top_ports"], [])
        self.assertIn("module top", (self.project / "rtl" / "top.v").read_text(encoding="utf-8"))
        self.assertIn("LOCATION = C4", (self.project / "constraints" / "generated.adc").read_text(encoding="utf-8"))
        self.assertEqual((self.project / "spec" / "design.md").read_text(encoding="utf-8"), "A short test design.\n")

    def test_cpu_trusted_harness_cannot_be_replaced_by_generic_model(self) -> None:
        path=self.project/'project.json'
        manifest=json.loads(path.read_text());manifest['verification']={'kind':'riscv-retire'}
        path.write_text(json.dumps(manifest))
        before=self.snapshot()
        with self.assertRaisesRegex(ValueError,'protected'):
            write_model_artifacts(self.project,self.payload())
        self.assertEqual(self.snapshot(),before)

    def test_records_actual_model_transport(self) -> None:
        payload=self.payload();payload['_generation']={'provider':'exchange','model':'gpt-6-luna'}
        write_model_artifacts(self.project,payload)
        manifest=json.loads((self.project/'project.json').read_text())
        self.assertEqual(manifest['model_backend'],{'provider':'exchange','model':'gpt-6-luna'})

    def test_commit_failure_restores_prior_files(self) -> None:
        before = self.snapshot()
        payload = self.payload()
        payload["files"] = {
            "rtl/old.v": "module top(input wire clk, output wire led); endmodule",
            "tb/old.v": "module tb_top; initial begin $fatal(1, \"self check\"); end endmodule",
        }
        real_replace = os.replace
        calls = 0

        def fail_second_replace(source, target):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("simulated commit failure")
            return real_replace(source, target)

        with patch("fpga_agent.project.os.replace", side_effect=fail_second_replace):
            with self.assertRaisesRegex(OSError, "simulated"):
                write_model_artifacts(self.project, payload)
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
