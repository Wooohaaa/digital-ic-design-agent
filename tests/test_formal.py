from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fpga_agent.cli import parser
from fpga_agent.formal import (
    formal_valid,
    lec_valid,
    resolve_formal_plan,
    resolve_lec_plan,
    run_formal,
    run_lec,
    tool_version,
    yosys_command,
)

COUNTER_GOOD = """module sync_fifo (input wire clk, input wire rst_n);
  reg [2:0] cnt;
  always @(posedge clk)
    if (!rst_n) cnt <= 3'd0;
    else if (cnt == 3'd4) cnt <= 3'd0;
    else cnt <= cnt + 1'b1;
  always @(posedge clk) assert(cnt <= 3'd4);
endmodule
"""

COUNTER_BAD = """module sync_fifo (input wire clk, input wire rst_n);
  reg [2:0] cnt;
  always @(posedge clk)
    if (!rst_n) cnt <= 3'd0;
    else cnt <= cnt + 1'b1;
  always @(posedge clk) assert(cnt <= 3'd4);
endmodule
"""

COUNTER_PLAIN = """module sync_fifo (input wire clk, input wire rst_n);
  reg [2:0] cnt;
  always @(posedge clk)
    if (!rst_n) cnt <= 3'd0;
    else cnt <= cnt + 1'b1;
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


class FormalBackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = build_project(self.root, COUNTER_GOOD)
        self.manifest = json.loads((self.project / "project.json").read_text(encoding="utf-8"))

    def require_yosys(self):
        if not yosys_command():
            self.skipTest("Yosys (yowasp-yosys or native) required")

    def test_missing_backend_reports_blocked_never_pass(self):
        with patch("fpga_agent.formal.yosys_command", return_value=[]):
            lec = run_lec(self.project)
            formal = run_formal(self.project)
        self.assertEqual(lec["status"], "blocked")
        self.assertEqual(formal["status"], "blocked")
        self.assertIn("Yosys", lec["reason"])
        self.assertTrue((self.project / "reports/lec.json").is_file())
        self.assertTrue((self.project / "reports/formal.json").is_file())

    def test_validators_reject_blocked_reports(self):
        with patch("fpga_agent.formal.yosys_command", return_value=[]):
            lec = run_lec(self.project)
            formal = run_formal(self.project)
        self.assertEqual(lec_valid(self.project, self.manifest, lec), (False, "LEC has not passed (blocked)"))
        self.assertEqual(formal_valid(self.project, self.manifest, formal)[0], False)

    def test_lec_plan_defaults_and_validation(self):
        plan = resolve_lec_plan(self.manifest)
        self.assertEqual(plan["mode"], "synth")
        self.assertEqual(plan["top"], "sync_fifo")
        designs = resolve_lec_plan(self.manifest, mode="designs", gold=["rtl/sync_fifo.v"],
                                   gate=["rtl/other.v"])
        self.assertEqual(designs["mode"], "designs")
        self.assertEqual(designs["gate_sources"], ["rtl/other.v"])
        with self.assertRaisesRegex(ValueError, "gate sources"):
            resolve_lec_plan(self.manifest, mode="designs", gold=["rtl/sync_fifo.v"])
        with self.assertRaisesRegex(ValueError, "top module"):
            resolve_lec_plan({}, top="")
        with self.assertRaisesRegex(ValueError, "'synth' or 'designs'"):
            resolve_lec_plan(self.manifest, mode="bogus")

    def test_formal_plan_defaults_and_validation(self):
        plan = resolve_formal_plan(self.manifest)
        self.assertEqual((plan["mode"], plan["depth"], plan["set_init_zero"]), ("bmc", 10, True))
        self.assertEqual(resolve_formal_plan(self.manifest, mode="prove")["mode"], "prove")
        self.assertFalse(resolve_formal_plan(self.manifest, set_init_zero=False)["set_init_zero"])
        with self.assertRaisesRegex(ValueError, "depth"):
            resolve_formal_plan(self.manifest, depth=0)
        with self.assertRaisesRegex(ValueError, "'bmc' or 'prove'"):
            resolve_formal_plan(self.manifest, mode="bogus")
        with self.assertRaisesRegex(ValueError, "top module"):
            resolve_formal_plan({})

    def test_lec_rejects_source_escaping_the_project(self):
        with self.assertRaisesRegex(ValueError, "inside the project"):
            run_lec(self.project, gold=["../outside.v"])

    def test_tool_version_is_recorded_when_available(self):
        command = yosys_command()
        if not command:
            self.skipTest("Yosys required")
        self.assertTrue(tool_version(command).startswith("Yosys"))

    def test_review_lists_optional_lec_and_formal_gates(self):
        from fpga_agent.quality import review_project
        report = review_project(self.project, "simulation", write=False)
        gates = {gate["id"]: gate for gate in report["gates"]}
        self.assertIn("DV-006", gates)
        self.assertIn("DV-007", gates)
        self.assertFalse(gates["DV-006"]["required"])
        self.assertFalse(gates["DV-007"]["required"])
        # No evidence recorded yet: reported as not_run, never as pass.
        self.assertEqual(gates["DV-006"]["status"], "not_run")
        self.assertEqual(gates["DV-007"]["status"], "not_run")


class FormalIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        if not yosys_command():
            self.skipTest("Yosys (yowasp-yosys or native) required")

    def test_lec_self_synthesis_passes_and_writes_netlist(self):
        project = build_project(self.root, COUNTER_GOOD)
        manifest = json.loads((project / "project.json").read_text(encoding="utf-8"))
        report = run_lec(project)
        self.assertEqual(report["status"], "pass", report["reason"])
        self.assertGreater(report["proven"], 0)
        self.assertEqual(report["unproven"], 0)
        self.assertTrue((project / report["netlist"]["path"]).is_file())
        self.assertEqual(lec_valid(project, manifest, report)[0], True)
        # Editing the RTL invalidates the recorded equivalence.
        (project / "rtl/sync_fifo.v").write_text(COUNTER_PLAIN, encoding="utf-8", newline="\n")
        valid, reason = lec_valid(project, manifest, report)
        self.assertFalse(valid)
        self.assertIn("changed", reason)

    def test_lec_designs_mode_detects_divergence(self):
        project = build_project(self.root, COUNTER_GOOD)
        (project / "rtl/revised.v").write_text(
            COUNTER_GOOD.replace("cnt == 3'd4", "cnt == 3'd3"), encoding="utf-8", newline="\n")
        report = run_lec(project, mode="designs", gold=["rtl/sync_fifo.v"], gate=["rtl/revised.v"])
        self.assertEqual(report["status"], "fail")
        self.assertGreater(report["unproven"], 0)

    def test_formal_bmc_pass_fail_and_not_applicable(self):
        project = build_project(self.root, COUNTER_GOOD)
        report = run_formal(project, mode="bmc", depth=12)
        self.assertEqual(report["status"], "pass", report["reason"])
        self.assertTrue(report["bounded"])
        self.assertEqual(report["assertions"], 1)
        self.assertTrue(formal_valid(project, json.loads((project / "project.json").read_text()), report)[0])

        (project / "rtl/sync_fifo.v").write_text(COUNTER_BAD, encoding="utf-8", newline="\n")
        self.assertEqual(run_formal(project, mode="bmc", depth=12)["status"], "fail")

        (project / "rtl/sync_fifo.v").write_text(COUNTER_PLAIN, encoding="utf-8", newline="\n")
        empty = run_formal(project, mode="bmc", depth=12)
        self.assertEqual(empty["status"], "not_applicable")
        self.assertEqual(empty["assertions"], 0)

    def test_formal_prove_mode_is_unbounded(self):
        project = build_project(self.root, COUNTER_GOOD)
        report = run_formal(project, mode="prove")
        self.assertEqual(report["status"], "pass", report["reason"])
        self.assertFalse(report["bounded"])
        self.assertIn("unbounded", report["reason"])

    def test_formal_unconstrained_init_rejects_reset_dependent_property(self):
        project = build_project(self.root, COUNTER_GOOD)
        report = run_formal(project, mode="bmc", depth=12, set_init_zero=False)
        self.assertEqual(report["status"], "fail")

    def test_bmc_depth_is_measured_in_clock_cycles(self):
        counter = """module m (input wire clk, input wire rst_n);
  reg [7:0] cnt;
  always @(posedge clk)
    if (!rst_n) cnt <= 8'd0;
    else cnt <= cnt + 1'b1;
  always @(posedge clk) assert(cnt != 8'd6);
endmodule
"""
        project = build_project(self.root, counter, name="m")
        # The counter is 6 at clock edge six, so a five-cycle bound misses it and
        # a six-cycle bound must catch it.
        self.assertEqual(run_formal(project, mode="bmc", depth=5)["status"], "pass")
        report = run_formal(project, mode="bmc", depth=6)
        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["steps"], 2 * 6 + 2)

    def test_memory_designs_are_lowered_before_sat(self):
        ram = """module m (input wire clk, input wire wr, input wire [3:0] addr,
           input wire [7:0] din, output reg [7:0] dout);
  reg [7:0] mem [0:15];
  always @(posedge clk) begin
    if (wr) mem[addr] <= din;
    dout <= mem[addr];
  end
  always @(posedge clk) assert(dout <= 8'hff);
endmodule
"""
        project = build_project(self.root, ram, name="m")
        report = run_formal(project, mode="bmc", depth=4)
        self.assertEqual(report["status"], "pass", report["reason"])
        self.assertEqual(report["assertions"], 1)

    def test_passing_reports_close_the_optional_gates(self):
        from fpga_agent.quality import review_project
        project = build_project(self.root, COUNTER_GOOD)
        self.assertEqual(run_lec(project)["status"], "pass")
        self.assertEqual(run_formal(project, mode="bmc", depth=12)["status"], "pass")
        report = review_project(project, "simulation", write=False)
        gates = {gate["id"]: gate for gate in report["gates"]}
        self.assertEqual(gates["DV-006"]["status"], "pass")
        self.assertEqual(gates["DV-007"]["status"], "pass")


class FormalCliTests(unittest.TestCase):
    def test_lec_and_formal_subcommands_parse(self):
        lec = parser().parse_args(["lec", "benchmarks/design", "--mode", "designs",
                                   "--gold", "a.v", "--gate", "b.v"])
        self.assertEqual((lec.command, lec.mode), ("lec", "designs"))
        self.assertEqual((lec.gold, lec.gate), (["a.v"], ["b.v"]))
        formal = parser().parse_args(["formal", "benchmarks/design", "--mode", "prove", "--depth", "32"])
        self.assertEqual((formal.command, formal.mode, formal.depth), ("formal", "prove", 32))
        self.assertIsNone(formal.init_zero)
        no_init = parser().parse_args(["formal", "benchmarks/design", "--no-init-zero"])
        self.assertFalse(no_init.init_zero)


if __name__ == "__main__":
    unittest.main()
