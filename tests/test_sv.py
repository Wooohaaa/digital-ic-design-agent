from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from fpga_agent.formal import run_formal, run_lec
from fpga_agent.quality import _verilator_command, lint_project
from fpga_agent.synth import run_synth
from fpga_agent.verify import verify_project
from fpga_agent.yosys import yosys_command

FIXTURE = Path(__file__).resolve().parent.parent / "examples" / "agent_validation" / "sv_datapath"

# One declaration per file keeps Verilator's DECLFILENAME rule satisfied, matching
# how a real SystemVerilog project distributes an interface and its users.
IFACE_FILES = {
    "rtl/bus_if.sv": '''interface bus_if #(parameter int W = 8) (input logic clk);
  logic [W-1:0] data;
  logic valid;
  modport src (output data, output valid, input clk);
  modport dst (input data, input valid, input clk);
endinterface
''',
    "rtl/iface_producer.sv": '''module iface_producer (bus_if.src b, input logic [7:0] d);
  always_ff @(posedge b.clk) begin b.data <= d; b.valid <= 1'b1; end
endmodule
''',
    "rtl/iface_consumer.sv": '''module iface_consumer (bus_if.dst b, output logic [7:0] q);
  always_ff @(posedge b.clk) if (b.valid) q <= b.data;
endmodule
''',
    "rtl/iface_top.sv": '''module iface_top (input logic clk, input logic [7:0] d, output logic [7:0] q);
  bus_if #(.W(8)) bus (.clk(clk));
  iface_producer u_p (.b(bus), .d(d));
  iface_consumer u_c (.b(bus), .q(q));
endmodule
''',
}

PKG_IMPORT_FILES = {
    "rtl/cfg_pkg.sv": '''package cfg_pkg;
  localparam int WIDTH = 8;
  typedef logic [WIDTH-1:0] word_t;
endpackage
''',
    "rtl/pkg_top.sv": '''module pkg_top (input logic clk, input logic rst_n, input logic [7:0] d, output logic [7:0] q);
  import cfg_pkg::*;
  word_t r;
  always_ff @(posedge clk) if (!rst_n) r <= '0; else r <= d;
  assign q = r;
endmodule
''',
}

PKG_QUALIFIED_FILES = {
    "rtl/cfg_pkg2.sv": '''package cfg_pkg2;
  localparam int WIDTH = 8;
  typedef logic [WIDTH-1:0] word_t;
endpackage
''',
    "rtl/pkgq_top.sv": '''module pkgq_top (input logic clk, input logic rst_n, input logic [7:0] d, output logic [7:0] q);
  cfg_pkg2::word_t r;
  always_ff @(posedge clk) if (!rst_n) r <= '0; else r <= d;
  assign q = r;
endmodule
''',
}

SV_PROPS = '''module sv_props #(
    parameter int unsigned LIMIT = 10
) (
    input  logic       clk,
    input  logic       rst_n,
    input  logic       en,
    output logic [3:0] cnt
);
    logic [3:0] r;
    always_ff @(posedge clk) begin
        if (!rst_n) r <= '0;
        else if (en) r <= (r == 4'(LIMIT - 1)) ? '0 : r + 4'd1;
        assert (r < 4'(LIMIT));
    end
    assign cnt = r;
endmodule
'''


def _iverilog_command() -> str | None:
    bundled = Path(__file__).resolve().parent.parent / ".tools" / "icarus" / "bin"
    env = os.environ.copy()
    if bundled.is_dir():
        env["PATH"] = str(bundled) + os.pathsep + env.get("PATH", "")
    return shutil.which("iverilog", path=env["PATH"])


def _build_project(root: Path, name: str, top: str, files: dict[str, str]) -> Path:
    project = root / name
    for relative, text in files.items():
        path = project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    (project / "project.json").write_text(json.dumps({
        "schema_version": 1, "project": name, "top_module": top,
        "rtl_sources": sorted(files),
    }, indent=2) + "\n", encoding="utf-8")
    return project


class SVDatapathFixtureTests(unittest.TestCase):
    """The pinned fixture must pass every vendor-neutral stage from one source."""

    def test_fixture_inputs_exist(self):
        for name in ("rtl/sv_datapath.sv", "tb/tb_sv_datapath.sv", "project.json",
                     "spec/verification-plan.json"):
            self.assertTrue((FIXTURE / name).is_file(), name)
        manifest = json.loads((FIXTURE / "project.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["rtl_sources"], ["rtl/sv_datapath.sv"])

    def test_icarus_self_checking_simulation_passes(self):
        if not _iverilog_command():
            self.skipTest("Icarus Verilog required")
        report = verify_project(FIXTURE)
        self.assertEqual(report.status, "pass", report.output[-3000:])
        self.assertTrue(report.checks and all(c["status"] == "pass" for c in report.checks))

    def test_verilator_strict_lint_passes(self):
        if not _verilator_command():
            self.skipTest("Verilator required")
        report = lint_project(FIXTURE)
        self.assertEqual(report["status"], "pass", report["output"][-2000:])

    def test_yosys_synthesis_passes_with_flops(self):
        if not yosys_command():
            self.skipTest("Yosys required")
        report = run_synth(FIXTURE)
        self.assertEqual(report["status"], "pass", report["reason"])
        self.assertGreater(report["flops"], 0)
        self.assertTrue((FIXTURE / report["netlist"]["path"]).is_file())

    def test_lec_rtl_vs_netlist_proves_equivalence(self):
        if not yosys_command():
            self.skipTest("Yosys required")
        report = run_lec(FIXTURE)
        self.assertEqual(report["status"], "pass", report.get("reason"))
        self.assertEqual(report["unproven"], 0)


class SVFormalTests(unittest.TestCase):
    """Immediate assertions in SystemVerilog RTL are checkable by Yosys."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_bmc_and_induction_prove_sv_property(self):
        if not yosys_command():
            self.skipTest("Yosys required")
        project = _build_project(self.root, "sv_props", "sv_props", {"rtl/sv_props.sv": SV_PROPS})
        bmc = run_formal(project, mode="bmc", timeout=120)
        self.assertEqual(bmc["status"], "pass", bmc.get("reason"))
        self.assertTrue(bmc["bounded"])
        prove = run_formal(project, mode="prove", timeout=120)
        self.assertEqual(prove["status"], "pass", prove.get("reason"))
        self.assertFalse(prove["bounded"])


class SVToolCapabilityTests(unittest.TestCase):
    """Pin the documented per-tool SystemVerilog support boundaries."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_icarus_compiles_package_and_import(self):
        iverilog = _iverilog_command()
        if not iverilog:
            self.skipTest("Icarus Verilog required")
        project = _build_project(self.root, "pkg_top", "pkg_top", PKG_IMPORT_FILES)
        sources = [str(project / name) for name in ("rtl/cfg_pkg.sv", "rtl/pkg_top.sv")]
        result = subprocess.run(
            [iverilog, "-g2012", "-s", "pkg_top", "-o", str(self.root / "a.vvp"), *sources],
            capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_verilator_lints_interface_with_modports(self):
        if not _verilator_command():
            self.skipTest("Verilator required")
        project = _build_project(self.root, "iface_top", "iface_top", IFACE_FILES)
        report = lint_project(project)
        self.assertEqual(report["status"], "pass", report["output"][-2000:])

    def test_yosys_synthesizes_interface_with_modports(self):
        if not yosys_command():
            self.skipTest("Yosys required")
        project = _build_project(self.root, "iface_top", "iface_top", IFACE_FILES)
        report = run_synth(project)
        self.assertEqual(report["status"], "pass", report["reason"])

    def test_yosys_synthesizes_qualified_package_type(self):
        if not yosys_command():
            self.skipTest("Yosys required")
        project = _build_project(self.root, "pkgq_top", "pkgq_top", PKG_QUALIFIED_FILES)
        report = run_synth(project)
        self.assertEqual(report["status"], "pass", report["reason"])

    def test_yosys_documented_limitation_package_import(self):
        # Documented boundary (docs/systemverilog.md): Yosys `read_verilog -sv`
        # accepts qualified package types but not `import pkg::*;`. If Yosys later
        # supports it, this test fails so the support matrix is updated.
        if not yosys_command():
            self.skipTest("Yosys required")
        project = _build_project(self.root, "pkg_top", "pkg_top", PKG_IMPORT_FILES)
        report = run_synth(project)
        self.assertEqual(report["status"], "error")


if __name__ == "__main__":
    unittest.main()
