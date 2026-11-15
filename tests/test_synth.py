from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fpga_agent.cli import parser
from fpga_agent.synth import resolve_synth_plan, run_synth, synth_valid, yosys_command

COUNTER = """module sync_fifo (input wire clk, input wire rst_n, output wire [2:0] q);
  reg [2:0] cnt;
  always @(posedge clk)
    if (!rst_n) cnt <= 3'd0;
    else if (cnt == 3'd4) cnt <= 3'd0;
    else cnt <= cnt + 1'b1;
  assign q = cnt;
endmodule
"""

COUNTER_WITH_ASSERT = COUNTER.replace(
    "endmodule", "  always @(posedge clk) assert(cnt <= 3'd4);\nendmodule")

COMBINATIONAL = """module sync_fifo (input wire [7:0] a, input wire [7:0] b, output wire [8:0] s);
  assign s = a + b;
endmodule
"""


def build_project(root: Path, rtl: str, *, name: str = "sync_fifo") -> Path:
    project = root / name
    (project / "rtl").mkdir(parents=True)
    (project / "rtl" / f"{name}.v").write_text(rtl, encoding="utf-8", newline="\n")
    (project / "project.json").write_text(json.dumps({
        "schema_version": 1, "project": name, "top_module": name,
        "rtl_sources": [f"rtl/{name}.v"],
    }, indent=2) + "\n", encoding="utf-8")
    return project


class SynthBackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = build_project(self.root, COUNTER)
        self.manifest = json.loads((self.project / "project.json").read_text(encoding="utf-8"))

    def test_missing_backend_reports_blocked_never_pass(self):
        with patch("fpga_agent.synth.yosys_command", return_value=[]):
            report = run_synth(self.project)
        self.assertEqual(report["status"], "blocked")
        self.assertIn("Yosys", report["reason"])
        self.assertIsNone(report["netlist"])
        self.assertTrue((self.project / "reports/synth.json").is_file())
        valid, reason = synth_valid(self.project, self.manifest, report)
        self.assertFalse(valid)
        self.assertIn("blocked", reason)

    def test_plan_defaults_and_validation(self):
        plan = resolve_synth_plan(self.manifest)
        self.assertEqual((plan["top"], plan["flatten"]), ("sync_fifo", True))
        self.assertEqual(plan["sources"], ["rtl/sync_fifo.v"])
        self.assertFalse(resolve_synth_plan(self.manifest, flatten=False)["flatten"])
        self.assertEqual(resolve_synth_plan(self.manifest, sources=["rtl/other.v"])["sources"], ["rtl/other.v"])
        with self.assertRaisesRegex(ValueError, "top module"):
            resolve_synth_plan({})

    def test_sources_outside_project_are_rejected(self):
        manifest = dict(self.manifest)
        manifest["synth"] = {"sources": ["../outside.v"]}
        (self.project / "project.json").write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "inside the project"):
            run_synth(self.project)


class SynthIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        if not yosys_command():
            self.skipTest("Yosys (yowasp-yosys or native) required")

    def test_synth_passes_reports_cells_and_netlist(self):
        project = build_project(self.root, COUNTER)
        manifest = json.loads((project / "project.json").read_text(encoding="utf-8"))
        report = run_synth(project)
        self.assertEqual(report["status"], "pass", report["reason"])
        self.assertGreater(report["cells"], 0)
        self.assertGreaterEqual(report["flops"], 3)
        netlist = project / report["netlist"]["path"]
        self.assertTrue(netlist.is_file())
        self.assertEqual(synth_valid(project, manifest, report), (True, "RTL synthesized to a current generic netlist"))
        # Editing the RTL invalidates the recorded netlist.
        (project / "rtl/sync_fifo.v").write_text(COUNTER.replace("3'd4", "3'd3"), encoding="utf-8", newline="\n")
        valid, reason = synth_valid(project, manifest, report)
        self.assertFalse(valid)
        self.assertIn("changed", reason)

    def test_tampered_netlist_is_rejected(self):
        project = build_project(self.root, COUNTER)
        manifest = json.loads((project / "project.json").read_text(encoding="utf-8"))
        report = run_synth(project)
        (project / report["netlist"]["path"]).write_text("// tampered\n", encoding="utf-8")
        valid, reason = synth_valid(project, manifest, report)
        self.assertFalse(valid)
        self.assertIn("modified", reason)

    def test_combinational_design_has_no_flops(self):
        project = build_project(self.root, COMBINATIONAL)
        report = run_synth(project)
        self.assertEqual(report["status"], "pass", report["reason"])
        self.assertEqual(report["flops"], 0)

    def test_assertions_are_stripped_from_the_netlist(self):
        project = build_project(self.root, COUNTER_WITH_ASSERT)
        report = run_synth(project)
        self.assertEqual(report["status"], "pass", report["reason"])
        netlist = (project / report["netlist"]["path"]).read_text(encoding="utf-8")
        self.assertNotIn("$check", netlist)
        self.assertNotIn("assert", netlist)

    def test_review_lists_optional_synth_gate(self):
        from fpga_agent.quality import review_project
        project = build_project(self.root, COUNTER)
        gates = {gate["id"]: gate for gate in review_project(project, "simulation", write=False)["gates"]}
        self.assertIn("SYNTH-001", gates)
        self.assertFalse(gates["SYNTH-001"]["required"])
        self.assertEqual(gates["SYNTH-001"]["status"], "not_run")
        run_synth(project)
        gates = {gate["id"]: gate for gate in review_project(project, "simulation", write=False)["gates"]}
        self.assertEqual(gates["SYNTH-001"]["status"], "pass")


class SynthCliTests(unittest.TestCase):
    def test_synth_subcommand_parses(self):
        args = parser().parse_args(["synth", "benchmarks/design", "--no-flatten", "--top", "core"])
        self.assertEqual((args.command, args.top), ("synth", "core"))
        self.assertFalse(args.flatten)
        plain = parser().parse_args(["synth", "benchmarks/design"])
        self.assertIsNone(plain.flatten)
        self.assertEqual(plain.timeout, 300)


if __name__ == "__main__":
    unittest.main()
